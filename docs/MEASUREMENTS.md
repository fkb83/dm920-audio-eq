# Messungen

Ohne Messgerät am HDMI-Ausgang lässt sich trotzdem exakt prüfen, was der EQ tut: Der Treiber enthält
`BAPE_OutputCapture`, mit dem sich der Ausgang eines Mixers in einen Speicherpuffer mitschneiden lässt.

## Messaufbau

Mit `insmod dm920eq.ko taps=1` legt das Modul zwei zusätzliche Mixer mit Capture-Ausgang an:

```
Decoder ─┬─▶ Zwischen-Mixer ─▶ EQ ─┬─▶ HDMI / S/PDIF / DAC
         │                         └─▶ Tap 1 (hinter dem EQ)
         └─▶ Tap 0 (vor dem EQ)
```

`cat /proc/dm920eq/capture` leert beide Puffer, wartet 200 ms und liefert dann gleich lange Stücke
beider Abgriffe (32-Bit-Stereo, 48 kHz, interleaved). Die Messung nutzt das laufende Fernsehprogramm
als Testsignal.

Auswertung:

```sh
ssh root@<box> cat /proc/dm920eq/capture > cap.bin
python3 tools/capan.py cap.bin   # Pegelverhältnis (ohne numpy)
python3 tools/tf.py cap.bin      # Frequenzgang (braucht numpy, z. B. im Build-Container)
```

`tf.py` richtet beide Signale per Kreuzkorrelation aus und schätzt den Frequenzgang über das
Kreuzspektrum (Welch, Hann-Fenster).

## Ergebnisse

### Pegel (Custom-Stage, nur `b0`)

| eingestellt | gemessen |
|---|---|
| × 0,25 (−12,04 dB) | −12,01 dB |
| × 0,5 (−6,02 dB) | −6,12 dB |
| × 1,0 (0 dB) | −0,05 dB |

Umgestellt wurde jeweils live, ohne Tonunterbrechung.

### Vorzeichen-Konvention

Testfilter `b0 = 0,1, a1 = 0,9`: gemessen ein Tiefpass (−4,1 dB bei 1 kHz, −21,3 dB bei 10 kHz).
Für `y = b0·x + a1·y[n-1]` ergibt die Theorie −4,05 dB bzw. −21,3 dB. Die a-Koeffizienten werden also
addiert.

### Drei Biquads hintereinander

Glocke 1 kHz −9 dB (Q 1,4), High-Shelf 8 kHz −6 dB, Low-Shelf 120 Hz −6 dB:

| Frequenz | 50 Hz | 100 Hz | 200 Hz | 500 Hz | 1 kHz | 2 kHz | 5 kHz | 10 kHz | 15 kHz | 20 kHz |
|---|---|---|---|---|---|---|---|---|---|---|
| berechnet | −5,8 | −4,1 | −1,0 | −1,8 | −9,0 | −1,8 | −0,9 | −4,5 | −5,9 | −6,0 |
| gemessen | −5,0 | −3,9 | −1,0 | −1,9 | −8,7 | −1,8 | −0,9 | −4,5 | −5,9 | −6,0 |

![Frequenzgang](images/measurement.svg)

Unter ~150 Hz streuen die Punkte etwas, weil ein 200-ms-Mitschnitt tiefe Frequenzen nur grob
auflöst.

### Über die Plugin-Oberfläche

Preset „Mehr Bass“ (+6/+4/+1 dB bei 60/150/400 Hz) per Fernbedienung gewählt, inklusive automatischer
Vorverstärkung von −6,1 dB:

| Frequenz | 50 Hz | 200 Hz | 500 Hz | 1 kHz | 5 kHz |
|---|---|---|---|---|---|
| berechnet | −1,7 | −3,0 | −5,0 | −5,9 | −6,1 |
| gemessen | −1,5 | −3,1 | −4,9 | −5,9 | −6,1 |

### Formatwechsel

Mit aktivem Filter (Glocke 1 kHz −9 dB) zwischen Tonspuren gewechselt; der Treiber baut dabei seine
Kette neu auf (`streamtype set`):

| Tonspur | EQ an HDMI / S/PDIF / DAC | gemessen bei 1 kHz |
|---|---|---|
| AC3 (Ausgangszustand) | ja | −9,0 dB |
| MPEG Stereo | ja | −9,0 dB |
| Dolby Digital 5.1 (Downmix) | ja | −8,9 dB |

## Was nicht gemessen wurde

Der Abgriff hinter dem EQ ist derselbe Connector, an dem auch der HDMI-Mixer hängt. Das analoge
bzw. digitale Signal *an der HDMI-Buchse* selbst wurde nicht mit externem Gerät gemessen.
