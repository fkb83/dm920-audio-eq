# Architektur und Treiber-Analyse

Dieses Dokument beschreibt, wie der Hardware-EQ der DM920 gefunden und angebunden wurde.
Alle Offsets gelten für `bcm7439.ko` Version `20200321-dm920` (Kernel `3.14-1.17-dm920`).

## Ausgangslage

- Live-TV-Ton läuft vom Demux direkt in den Audio-DSP (Broadcom *Raaga*) und von dort über die
  *FMM* (Audio-Mixing-Hardware) zu HDMI und S/PDIF. Die CPU sieht den Ton nie – ein Software-EQ
  könnte sich nicht dazwischenhängen.
- `/proc/stb/audio` bietet nur Format-Auswahl und Delays, keine Klangregelung.
- Der Treiber `bcm7439.ko` ist proprietär, aber mit Symbolen gebaut. Er enthält die komplette
  Broadcom-Audio-Bibliothek *BAPE* einschließlich `BAPE_Equalizer_*`, `BAPE_EqualizerStage_*`,
  `BAPE_TruVolume_*`, `BAPE_DolbyVolume_*` und der Koeffizienten-Generatoren `EQ_generate_*`.
  Keine dieser Equalizer-Funktionen wird vom Treiber aufgerufen.

## Die Audiokette des Treibers

`bcm_audio_init()` legt pro Ausgang einen Mixer an, `bcm_audio_decoder_port_setup()` hängt den
passenden Decoder-Ausgang („Connector“) an den Mixer. Der Treiber loggt das selbst:

```
audio ch0: add stereo input to mixer MAI
```

Relevante Felder (Basis `ctx` = Audio-Kontext des Treibers):

| Adresse | Inhalt |
|---|---|
| `ctx + 0x1c30` (`dev`) | BAPE-Gerätehandle (`BAPE_Handle`) |
| `dev + 0x0c/0x10` | Port 0 „DUMMY“: aktueller Eingang / Mixer |
| `dev + 0x1c/0x20` | Port 1 „DUMMY2“ |
| `dev + 0x2c/0x30` | Port 2 „DAC“ |
| `dev + 0x3c/0x40` | Port 4 „SPDIF“ |
| `dev + 0x4c/0x50` | Port 3 „MAI“ = **HDMI** |
| `dev + 0xf0` (`dec`) | Decoder 0 (`dec+0` zeigt zurück auf `ctx`, `dec+0xf0` = Index, `dec+0xf4` = läuft) |
| `dev + 0xf8 … 0x10c` | Decoder-Connectoren: mono, **stereo**, Mehrkanal, compressed, 4x, 16x |
| `ctx + 0x1e90` | Mutex des Decoders (derselbe, den der `ioctl`-Pfad nimmt) |

### Wie das Modul `ctx` findet

`bcm_audio_init()` registriert `/proc/stb/audio/audio_delay_pcm` über `stb_proc_simple_add()` aus
`stb_core.ko` und schreibt danach `ctx` an Offset `0x24` des zurückgegebenen Eintrags. Dieser Eintrag
ist zugleich `PDE_DATA` der proc-Datei. Das Modul öffnet also die proc-Datei und liest:

```c
ctx = *(char **)((char *)PDE_DATA(file_inode(f)) + 0x24);
```

Vor jeder Benutzung wird geprüft: `dec+0 == ctx`, Decoder-Index 0, und das BAPE-Handle steht an
beiden bekannten Stellen identisch. Passt etwas nicht, lädt das Modul nicht.

### Funktionen

Die BAPE-Funktionen sind lokale Symbole, aber über `/proc/kallsyms` sichtbar; der Kernel exportiert
`kallsyms_lookup_name`, damit werden sie zur Laufzeit aufgelöst. Die Prototypen entsprechen der
Broadcom-Referenzsoftware und wurden aus dem Maschinencode bestätigt:

```c
int  BAPE_Equalizer_Create(BAPE_Handle, const BAPE_EqualizerSettings *, BAPE_EqualizerHandle *);
int  BAPE_EqualizerStage_Create(BAPE_Handle, const BAPE_EqualizerStageSettings * /*180 Bytes*/, ...);
void BAPE_EqualizerStage_GetDefaultSettings(BAPE_EqualizerStageType, BAPE_EqualizerStageSettings *);
int  BAPE_EqualizerStage_SetSettings(BAPE_EqualizerStageHandle, const BAPE_EqualizerStageSettings *);
int  BAPE_Equalizer_SetStages(BAPE_EqualizerHandle, BAPE_EqualizerStageHandle *, unsigned num /*<=12*/);
int  BAPE_Mixer_Create(BAPE_Handle, const BAPE_MixerSettings * /*240 Bytes*/, BAPE_MixerHandle *);
int  BAPE_Mixer_AddInput(BAPE_MixerHandle, BAPE_Connector, const BAPE_MixerAddInputSettings *);
int  bcm_audio_decoder_stop(void *dec);  /* Treiberintern */
int  bcm_audio_decoder_start(void *dec);
```

## Warum ein Zwischen-Mixer?

Der erste Versuch, den EQ direkt an den Stereo-Connector des Decoders zu hängen, scheiterte mit
Fehler 8. Grund: Der EQ akzeptiert nur Eingänge der Quelle *FCI* (FMM-intern, `BAPE_FMT_P_EnableSource(…, 3)`),
der Decoder liefert aber aus einem DSP-Puffer. Broadcoms eigene Nexus-Schicht löst das genauso:

```
Decoder (DSP-Puffer) ─▶ Zwischen-Mixer (FCI) ─▶ Equalizer ─▶ Ausgangs-Mixer ─▶ HDMI/S/PDIF/DAC
```

## Ablauf beim Laden

1. Equalizer, eine *Custom*-Stage und den Zwischen-Mixer anlegen
2. Decoder-Mutex nehmen, laufenden Decoder anhalten
3. Stereo-Connector in den Zwischen-Mixer, an allen Ausgangs-Mixern, die bisher „stereo“ bekamen,
   „stereo“ durch den EQ-Ausgang ersetzen
4. **Den Stereo-Connector des Decoders (`dev+0xfc`) auf den EQ-Ausgang umbiegen.** Damit verdrahtet
   `bcm_audio_decoder_port_setup()` bei jedem späteren Formatwechsel von sich aus den EQ.
5. Decoder starten, Mutex freigeben (Tonaussetzer ca. 20 ms)

Beim Entladen läuft alles rückwärts.

## Equalizer-Stage „Custom“

Die Stage vom Typ 5 nimmt bis zu 8 Biquads mit freien Koeffizienten. Layout der 180-Byte-Struktur:

| Offset | Inhalt |
|---|---|
| `0x00` | Typ (0 Tone, 1 Five-Band, 2 Seven-Band, 3 Subsonic, 4 Subwoofer, **5 Custom**) |
| `0x04` | Bypass (`bool`, im laufenden Betrieb nicht umschaltbar) |
| `0x08` | Ramp-Schritte (Default 10) |
| `0x10` | Anzahl Biquads (1–8) |
| `0x14 + 20·i` | Biquad *i*: `b0, b1, b2, a1, a2` als `int32`, **Q5.26** (1.0 = `0x04000000`) |

Die Hardware rechnet

```
y[n] = b0·x[n] + b1·x[n-1] + b2·x[n-2] + a1·y[n-1] + a2·y[n-2]
```

– die a-Koeffizienten werden also **addiert** (Vorzeichen umgekehrt zur Lehrbuch-Form). Das wurde
mit einem Testfilter `b0 = 0,1, a1 = 0,9` gemessen: Tiefpass mit −4,1 dB bei 1 kHz (theoretisch −4,05 dB).

`BAPE_EqualizerStage_SetSettings()` überträgt neue Koeffizienten im laufenden Betrieb.

## Kernel-Schnittstelle `/proc/dm920eq/`

| Datei | Richtung | Inhalt |
|---|---|---|
| `biquads` | schreiben | `N c0 c1 … c(5N-1)` – N Biquads, je `b0 b1 b2 a1 a2` als Q5.26-Ganzzahlen |
| `gain` | lesen/schreiben | einfacher Pegel in Promille (0–1000, nur absenken) |
| `status` | lesen | welcher Ausgang am EQ hängt, ob die Treiber-Umleitung aktiv ist |
| `capture` | lesen | nur mit `taps=1`: Mitschnitt vor und hinter dem EQ (siehe [MEASUREMENTS.md](MEASUREMENTS.md)) |

Beispiel – eine Glocke bei 1 kHz mit −6 dB:

```sh
python3 tools/peq.py peak:1000:-6:1.4
# -> 1 65040809 -124848508 60885011 124848508 -58816957
ssh root@<box> "echo '1 65040809 -124848508 60885011 124848508 -58816957' > /proc/dm920eq/biquads"
```

Das Modul selbst begrenzt Koeffizienten nicht – die Begrenzung der Gesamtverstärkung auf ≤ 0 dB
macht das Plugin (`eqmath.design()`), bzw. `peq.py` warnt.

## Modulparameter

| Parameter | Default | Bedeutung |
|---|---|---|
| `gain_permille` | 1000 | Startpegel |
| `taps` | 0 | 1 = zwei Mitschnitt-Abgriffe (vor/hinter EQ) für Messungen anlegen |

## Grenzen

- Nur PCM-Stereo vom Hauptdecoder. Passthrough und Mehrkanal-PCM laufen am EQ vorbei.
- Koeffizienten sind für 48 kHz gerechnet (DVB). Bei 44,1 kHz verschieben sich Frequenzen um ~8 %.
- Ungetestet: Standby/Aufwachen, Mediaplayer, Bild-in-Bild, Transcoding.
