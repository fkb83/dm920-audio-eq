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

## Werbe-Bremse (Lautstärke-Automatik)

Die fertigen Pegelregler der BAPE-Bibliothek (`BAPE_AutoVolumeLevel`, `BAPE_TruVolume`,
`BAPE_DolbyVolume`) sind im Treiber zwar vorhanden, laufen aber auf dem Audio-DSP – und dessen Firmware
(`BDSP_IMG_*`) enthält nur Decoder, Mixer, SRC und Fade, **nicht** AVL, TruVolume oder Dolby Volume.
Deshalb regelt das Modul selbst:

```
Decoder ─stereo─▶ Zwischen-Mixer (Eingangslautstärke = Regelgröße) ─▶ EQ ─▶ HDMI-/S/PDIF-Mixer
                                                                       └──▶ DAC-Mixer ─┬─▶ DAC
                                                                                       └─▶ OutputCapture #2 ─▶ Kernel-Thread
```

- **Messpunkt:** Der Abgriff ist ein zusätzlicher Ausgang des DAC-Mixers des Treibers, liegt also hinter
  EQ und Stellglied. Ein eigener Mixer direkt am Decoder (so bis v1.1.0) hält den PCM-Zweig des Decoders
  auch dann aktiv, wenn der Treiber auf DD+/DTS-Passthrough schaltet. Beim Zurückschalten auf PCM stürzt
  der Audio-DSP dann im Sekundentakt ab und wird vom Treiber-Watchdog neu gestartet (gemessen: 42 Resets in
  20 s, ohne Modul 0). Den DAC-Mixer legt der Treiber im Passthrough selbst still.
- **Messung:** Der Thread liest alle ~30 ms den Capture-Puffer (32-Bit-Stereo, 48 kHz), filtert mit der
  K-Gewichtung aus ITU-R BS.1770 (zwei Biquads, Q28-Festkomma) und bildet alle 40 ms einen Block. Die
  eigene Absenkung wird pro Block wieder herausgerechnet. Der EQ verschiebt Messwert und Bezugspegel
  gleichermaßen und löst die Bremse deshalb nicht aus. Die Box-Lautstärke wirkt nur am DAC-Ausgang und
  nicht am Abgriff. Kurzzeit-Lautheit: exponentiell geglättet (~320 ms). Abgleich gegen eine
  Python-Referenz: ±1 dB.
- **Bezugspegel:** nach Umschalten/Stille Mittelwert der ersten 3 s, dann langsamer Nachlauf
  (+0,6 dB/min, −1,2 dB/min; in der ersten Minute 10× schneller).
- **Kennlinie:** Liegt die Lautheit mehr als *margin* über dem Bezug, wird mit *ratio* komprimiert,
  begrenzt auf *maxred*. Attack ≈ 0,25 s (exponentiell), Release 1,5 dB/s. Nie Verstärkung > 0 dB.
- **Stellglied:** `BAPE_Mixer_SetInputVolume()` am Zwischen-Mixer (264-Byte-Struktur: 8×8-Matrix in
  1.23-Festkomma, `0x800000` = 1,0; die Diagonale wird skaliert). Die Treiber-Lautstärke von Enigma2
  wirkt dagegen über `BAPE_SetOutputVolume()` an den Ausgängen – die beiden kommen sich nicht in die Quere.

Logarithmus und Verstärkungsfaktoren kommen aus Tabellen (`kernel/dm920eq_tables.h`,
erzeugt von `tools/gen_tables.py`), weil im Kernel keine Fließkommazahlen erlaubt sind.

## Sperren und Treiber-Ereignisse

Der Treiber ruft seine Ereignis-Handler (u. a. den Neustart des Audio-DSP nach einem Watchdog-Alarm)
aus `b_event_task` mit seiner globalen Sperre `b_lock` auf; der Watchdog-Handler nimmt danach die
Decoder-Mutexe. Senderwechsel und Formatwechsel laufen unter dem Decoder-Mutex. Beide bauen Mixer-
Ausgänge ab und wieder auf, auch den Mitschnitt-Puffer (`BAPE_OutputCapture_P_Disable` gibt die
DFIFO-Gruppe ohne Sperre frei, `BAPE_OutputCapture_ConsumeData` prüft „aktiv?“ vor der Sperre).

Bis v1.1.0 rief der Thread die Capture-Funktionen ohne diese Sperren auf. Am 23.11.2026 traf er dabei
einen DSP-Neustart: NULL-Zeiger in `BAPE_DfifoGroup_P_CommitData_isr` innerhalb der Treiber-
Critical-Section, die danach nie wieder freigegeben wurde – Grafik-Thread von Enigma2 und Treiber-Threads
hingen, die Box reagierte nicht mehr. Seit v1.2.0 läuft jeder BAPE-Aufruf des Moduls unter `b_lock` und
dann dem Decoder-Mutex (dieselbe Reihenfolge wie im Treiber). Der Thread kopiert die Messdaten unter Sperre
und rechnet ohne Sperre. Nur Laden/Entladen nimmt allein den Decoder-Mutex, weil dort der Decoder
gestoppt und gestartet wird.

## Wenn der Treiber Ausgänge umhängt

Der BAPE-Equalizer verarbeitet nur für die Abnehmer, die er hatte, als sein Eingang angeschlossen wurde.
Entfernt der Treiber den EQ-Connector aus einem Ausgangs-Mixer und hängt ihn wieder an, bekommt dieser
Ausgang den **unbearbeiteten** Ton – Filter und Stellglied wirken dort nicht mehr (gemessen). Das passiert:

| Auslöser | was der Treiber tut |
|---|---|
| DD+/DTS-Passthrough und zurück | EQ von allen Ausgängen ab, beim Zurückschalten wieder an |
| HDMI-Eingang starten | an HDMI und S/PDIF den aktuellen Eingang durch den HDMI-Eingang ersetzen (DAC bleibt) |
| HDMI-Eingang beenden | den TV-Connector anhängen – nur wenn dort nicht schon der TV-Connector hängt |

Bis v1.2.0 hat der Thread nur Passthrough → PCM bemerkt (dort waren *alle* Ausgänge weg). Beim HDMI-
Eingang blieb der DAC am EQ, der Wechsel blieb unbemerkt, und HDMI/S/PDIF liefen danach roh.

Seit v1.3.0 prüft der Thread jede Runde jeden PCM-Ausgang und ruft `rebuild()`, wenn

- ein Ausgang neu an den EQ gehängt wurde, oder
- ein Ausgang den rohen TV- oder HDMI-Eingangs-Connector bekommen hat.

`rebuild()` nimmt die Decoder-Mutexe (TV, dann HDMI-Eingang – Reihenfolge wie im Treiber), hält beide
Decoder an (TV über `bcm_audio_decoder_stop`, HDMI-Eingang über `BAPE_Decoder_Stop` – so wie der Treiber
selbst bei einem Formatwechsel des HDMI-Eingangs), hängt alle PCM-Ausgänge an den EQ, schließt den EQ-
Eingang neu an und startet wieder, was lief. Danach ist der Zustand so, dass der Treiber beim Beenden des
HDMI-Eingangs nichts mehr umhängt (an HDMI hängt schon „sein“ TV-Connector). Kosten: eine kurze Tonpause
direkt beim Umschalten. Höchstens ein `rebuild()` pro Sekunde.

Versucht und verworfen:

- **Fester Mixer hinter dem EQ**, damit der EQ nie neue Abnehmer bekommt: Ein Mixer-Connector versorgt
  hier nur einen weiteren Mixer – S/PDIF und DAC blieben stumm.
- **Neu anschließen, ohne die Decoder anzuhalten:** `BAPE_Equalizer_RemoveInput` scheitert bei laufender
  Quelle; der halbe Umbau beschädigte die Verbindungsliste des Treibers (Endlosschleife beim Entladen).

## HDMI-Eingang

Der Treiber spielt den HDMI-Eingang auf seinem Decoder 1. Relevante Stellen (relativ zu `dev`):

| Offset | Inhalt |
|---|---|
| `+0x29c` | Stereo-Connector des HDMI-Eingangs (im Log „stereo (hdmi-rx)“) |
| `+0x294` | BAPE-Decoder-Handle; Zustand bei Handle `+0x3e4` (0 = gestoppt) |
| `+0x300` | Start-Einstellungen, mit denen der Treiber den Decoder startet |
| `+0x400` | Mutex von Decoder 1 |

Das Modul hängt diesen Connector zusätzlich an den Zwischen-Mixer (nur bei gestopptem Decoder; läuft der
HDMI-Eingang gerade, holt `rebuild()` das nach). Den Connector-Eintrag des Treibers lässt es
unverändert: Ein umgebogener Eintrag führte dazu, dass der Treiber beim Start des HDMI-Eingangs gar nicht
mehr verdrahtete und die Ausgänge nicht startete. Die Werbe-Bremse regelt nur den TV-Eingang des
Zwischen-Mixers. Solange der HDMI-Eingang spielt (und der TV-Decoder steht), ignoriert sie den Abgriff und
steht auf 0 dB; `/proc/dm920eq/agc` meldet dann `hdmi_input 1`.

Gestartet und beendet wird der HDMI-Eingang vom New-Nigma-Plugin (`Plugins/newnigma2/hdmi`, Funktion
`HDMID(session)` öffnet die Wiedergabe direkt, `HdmiMain.leave()` beendet sie). Das EQ-Plugin ruft genau
diese Funktionen für die Radio-Taste und die Web-Seite auf.

## Kernel-Schnittstelle `/proc/dm920eq/`

| Datei | Richtung | Inhalt |
|---|---|---|
| `biquads` | schreiben | `N c0 c1 … c(5N-1)` – N Biquads, je `b0 b1 b2 a1 a2` als Q5.26-Ganzzahlen |
| `gain` | lesen/schreiben | einfacher Pegel in Promille (0–1000, nur absenken) |
| `status` | lesen/schreiben | lesen: welcher Ausgang am EQ hängt, Treiber-Umleitung, HDMI-Eingang; schreiben `1`: `rebuild()` auslösen (Diagnose) |
| `agc` | lesen/schreiben | Werbe-Bremse: schreiben `0`–`3` (aus/leicht/mittel/stark) oder `4 <margin_mdB> <ratio×10> <maxred_mdB>`; lesen liefert Lautheit, Bezug, aktuelle Absenkung, unkorrigierten Messwert (`measured`) und gesetzte Absenkung (`applied`) in milli-dB |
| `capture` | lesen | nur mit `taps=1`: Mitschnitt vor und hinter dem EQ (siehe [MEASUREMENTS.md](MEASUREMENTS.md)) |
| `level` | lesen | nur mit `maitap=1`: mittlerer Pegel am HDMI-Ausgangs-Mixer über 300 ms (`<mdB> <bytes>`) |
| `conn` | lesen | Diagnose: Connector-Zeiger des Treibers und der Kette |

Beispiel – eine Glocke bei 1 kHz mit −6 dB:

```sh
python3 tools/peq.py peak:1000:-6:1.4
# -> 1 65040809 -124848508 60885011 124848508 -58816957
ssh root@<box> "echo '1 65040809 -124848508 60885011 124848508 -58816957' > /proc/dm920eq/biquads"
```

Das Modul selbst begrenzt Koeffizienten nicht. Das Plugin arbeitet standardmäßig wie ein echter EQ
(Anhebung = lauter); mit „Übersteuerungsschutz“ rechnet `eqmath.design()` eine negative Vorverstärkung
ein. Bei Übersteuerung begrenzt die Hardware sauber auf Vollaussteuerung (siehe MEASUREMENTS.md).

## Modulparameter

| Parameter | Default | Bedeutung |
|---|---|---|
| `gain_permille` | 1000 | Startpegel |
| `taps` | 0 | 1 = zwei Mitschnitt-Abgriffe (vor/hinter EQ) für Messungen anlegen. Nur für Messungen: diese Abgriffe hängen direkt am Decoder und lösen beim Wechsel von DD+-Passthrough auf PCM DSP-Abstürze aus |
| `agc` | 1 | 0 = keine Werbe-Bremse (kein Abgriff); `rebuild()` läuft trotzdem |
| `hdmiin` | 1 | 0 = HDMI-Eingang nicht durch den EQ führen |
| `maitap` | 0 | Messabgriff am Ausgangs-Mixer für `/proc/dm920eq/level`: 1/3 = HDMI, 4 = S/PDIF (nur beim Laden im TV-Betrieb; die Mixer müssen dafür stehen) |

## Grenzen

- Nur PCM-Stereo vom Hauptdecoder. Passthrough und Mehrkanal-PCM laufen am EQ vorbei.
- Koeffizienten sind für 48 kHz gerechnet (DVB). Liefert der HDMI-Eingang 44,1 kHz (Musik), verschieben
  sich die Frequenzen um ~8 %, falls der Zwischen-Mixer mit der Eingangsrate läuft (nicht gemessen).
- Getestet: Senderwechsel MPEG/AC3/DD+ (Passthrough) in beliebiger Reihenfolge, TV ↔ HDMI-Eingang (auch
  direkt aus einem DD+-Sender), Standby/Aufwachen, Laden des Moduls während der HDMI-Eingang läuft,
  DSP-Watchdog-Neustarts.
- Ungetestet: Mediaplayer, Bild-in-Bild, Transcoding, HDMI-Eingang mit Mehrkanal-/Bitstream-Ton.
