# DM920 Audio EQ

**Hardware-Equalizer für die Dreambox DM920** – ein Kernelmodul plus Enigma2-Plugin, das den
im Broadcom-Audiochip vorhandenen, aber nie freigeschalteten Equalizer nutzbar macht.

Der EQ sitzt direkt im Audiopfad zwischen Decoder und den Ausgängen **HDMI, S/PDIF und DAC**.
Er rechnet in der Audio-Hardware des Chips – ohne CPU-Last, ohne Verzögerung, für Live-TV,
Aufnahmen und jeden Sender.

<p align="center"><img src="docs/images/plugin-ui.png" width="640" alt="Plugin-Oberfläche"></p>

> *English summary:* Kernel module + Enigma2 plugin that enables the unused Broadcom BAPE hardware
> equalizer of the Dreambox DM920 (BCM7439) and inserts it between the audio decoder and the
> HDMI / S/PDIF outputs. 8-band parametric EQ, presets, automatic headroom so the output never gets
> louder than without EQ. Verified by measuring the audio before and after the EQ inside the chip.

## Funktionen

- **8 Bänder** (60 Hz … 15 kHz, Shelf + Glockenfilter), je ±12 dB, Änderungen sofort hörbar
- **Voreinstellungen:** Neutral, Mehr Bass, Sprache/Dialog, Loudness, Mehr Höhen, Nachtmodus
- **Automatische Vorverstärkung:** die Gesamtverstärkung bleibt immer ≤ 0 dB – der EQ kann nicht übersteuern
  und macht nichts lauter, als es ohne EQ wäre (wichtig, wenn Lautsprecher direkt dranhängen)
- **Gesamtpegel** zum zusätzlichen Absenken
- Übersteht Senderwechsel und Wechsel des Tonformats (MPEG ↔ AC3 ↔ Dolby Digital 5.1 Downmix)
- Startet automatisch mit Enigma2, Einstellungen bleiben gespeichert

## Gemessen, nicht geschätzt

Der Treiber enthält eine Mitschnittfunktion (`BAPE_OutputCapture`). Damit wurde das Audiosignal
gleichzeitig **vor und hinter dem EQ im Chip** aufgezeichnet und der Frequenzgang berechnet:

<p align="center"><img src="docs/images/measurement.svg" width="760" alt="Frequenzgang gemessen vs. berechnet"></p>

Messung und Rechnung stimmen auf Zehntel-dB überein (Details: [docs/MEASUREMENTS.md](docs/MEASUREMENTS.md)).

## Voraussetzungen

| | |
|---|---|
| Receiver | Dreambox **DM920** |
| Image | OE 2.5 (getestet mit **New Nigma 2**) |
| Kernel | `3.14-1.17-dm920` |
| Audiotreiber | `bcm7439.ko` Version `20200321-dm920` |
| Zugang | SSH als `root` |

Das Modul bindet sich an interne Strukturen genau dieses Treiberstands. Es prüft beim Laden, ob alles
passt, und verweigert sich andernfalls, statt etwas kaputtzumachen. Andere Boxen (DM900 o. ä.) sind
ungetestet.

## Installation

**Mit fertigem Modul** (aus den [Releases](../../releases)):

```sh
git clone https://github.com/fkb83/dm920-audio-eq.git
cd dm920-audio-eq
mkdir -p dist && cp ~/Downloads/dm920eq.ko dist/
./install.sh <IP-der-Box>
```

**Oder selbst bauen** (braucht nur Docker, siehe [docs/BUILD.md](docs/BUILD.md)):

```sh
./build/build.sh
./install.sh <IP-der-Box>
```

`install.sh` kopiert das Plugin nach `/usr/lib/enigma2/python/Plugins/Extensions/HardwareEQ/`
und startet Enigma2 neu.

## Bedienung

**Blaue Taste → Erweiterungen → Hardware-Equalizer**

| Taste | Funktion |
|---|---|
| ◀ ▶ | Wert ändern (sofort hörbar) |
| Grün | Speichern |
| Rot / Exit | Verwerfen (zurück zum gespeicherten Stand) |
| Gelb | Alle Bänder auf Neutral |
| Blau | EQ an/aus |

Unten zeigt das Plugin die aktuelle Vorverstärkung und auf welchen Ausgängen der EQ gerade wirkt.

### Wichtig: Downmix einstellen

Der EQ bearbeitet **PCM-Ton**. Wird Ton als Bitstream (Passthrough) oder Mehrkanal-PCM ausgegeben,
läuft er am EQ vorbei. Für einen EQ auf allen Sendern in den Audio-Einstellungen der Box für
AC3, AAC, DD+ und DTS **„Downmix“** wählen. Das Plugin zeigt an, wenn der EQ auf keinem Ausgang aktiv ist.

## Wie es funktioniert

Kurzfassung – ausführlich in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md):

```
vorher:   Decoder ──stereo──▶ HDMI-Mixer ──▶ HDMI
nachher:  Decoder ──stereo──▶ Zwischen-Mixer ──▶ BAPE-Equalizer ──▶ HDMI-/S/PDIF-/DAC-Mixer
```

1. Der Broadcom-Audiotreiber `bcm7439.ko` enthält die komplette *BAPE*-Audio-Engine inklusive
   Equalizer (Glocke, Shelf, Hoch-/Tiefpass, 8 frei programmierbare Biquads) – benutzt sie aber nie.
2. `dm920eq.ko` findet die internen Funktionen per `kallsyms_lookup_name` und den Audio-Kontext des
   Treibers über einen seiner `/proc/stb/audio`-Einträge.
3. Es hält den Decoder kurz an, baut die Kette um und trägt den EQ-Ausgang dort ein, wo der Treiber
   seinen Stereo-Ausgang verwaltet. Dadurch verdrahtet der Treiber bei jedem Formatwechsel selbst den EQ.
4. Das Plugin rechnet die Bänder (RBJ-Biquads) in Festkomma-Koeffizienten um und schreibt sie nach
   `/proc/dm920eq/biquads`. Die Hardware übernimmt sie im laufenden Betrieb.

Entladen (`rmmod dm920eq`) stellt den Originalzustand wieder her.

## Repository

| Pfad | Inhalt |
|---|---|
| [`kernel/`](kernel) | Kernelmodul `dm920eq.c` |
| [`plugin/HardwareEQ/`](plugin/HardwareEQ) | Enigma2-Plugin (Python 2.7) und Filterberechnung `eqmath.py` |
| [`build/`](build) | Docker-Build-Umgebung, Kernel-Vorbereitung mit Prüfsummen |
| [`tools/`](tools) | Koeffizienten-Rechner und Messauswertung |
| [`docs/`](docs) | Architektur, Build, Messungen |

## Haftungsausschluss

Dies ist ein inoffizielles Hobbyprojekt, nicht verbunden mit Dream Property GmbH oder Broadcom.
Es greift in einen geschlossenen Kernel-Treiber ein. Nutzung auf eigenes Risiko – im schlimmsten
Fall startet die Box neu; dauerhaft verändert wird nichts außer dem Plugin-Ordner.
Die Treiber-Interna wurden zur Herstellung von Interoperabilität analysiert; Teile des
Original-Treibers sind nicht enthalten.

## Lizenz

[GPL-2.0](LICENSE)
