# Bauen

Das Modul muss exakt gegen den Kernel der Box gebaut werden (`3.14-1.17-dm920`, GCC 5).
Alles läuft in Docker, auf dem Host wird nur Docker gebraucht.

```sh
./build/build.sh
```

Ergebnis: `dist/dm920eq.ko`. Der erste Lauf lädt ca. 80 MB und dauert etwa eine Minute,
danach wird der vorbereitete Kernel-Tree im Docker-Volume `dm920-audio-eq-kernel` wiederverwendet.

## Was passiert

[`build/prepare-kernel.sh`](../build/prepare-kernel.sh) lädt und prüft (SHA-256):

| Datei | Quelle |
|---|---|
| `linux-3.14.tar.xz` | kernel.org |
| `patch-3.14.79.xz` | kernel.org |
| `linux-dreambox-3.14-6fa88d20….patch.xz` | Dream-Multimedia-Kernelpatch, Mirror [oe-mirrors/dreambox](https://github.com/oe-mirrors/dreambox) (Original-Server ist offline) |
| `defconfig` `dm920` | [wzcwzc/meta-dream](https://github.com/wzcwzc/meta-dream) |

Dann: entpacken, patchen, `SUBLEVEL` aus dem Makefile entfernen (die Box meldet sich als
`3.14-1.17-dm920`), `olddefconfig`, `modules_prepare`. Das Skript bricht ab, wenn die
Kernel-Release-Kennung nicht exakt passt.

Die Prüfsummen von Kernel, Stable-Patch und Dream-Patch sind dieselben wie im Original-Rezept
`linux-dreambox_3.14.bb`.

## Kompatibilität prüfen

Ein gebautes Modul muss diese `vermagic` haben:

```
vermagic=3.14-1.17-dm920 SMP mod_unload ARMv7 p2v8
```

Auch das Layout von `struct module` wurde gegen ein Original-Modul der Box verglichen
(`.gnu.linkonce.this_module`: 0x150 Bytes, `init` bei 0xc0, `exit` bei 0x148) – identisch.

## Ohne Docker

Jede ARM-Hard-Float-Toolchain mit GCC 5 funktioniert, z. B.:

```sh
make -C /pfad/zu/linux-3.14 ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- M=$PWD/kernel modules
```

Neuere GCC-Versionen werden vom 3.14-Kernel nicht unterstützt.
