#!/bin/sh
# Installs the Hardware-Equalizer plugin (and dm920eq.ko) on a Dreambox DM920.
# Usage: ./install.sh <box-ip> [path/to/dm920eq.ko]
# The box must accept SSH logins as root. Enigma2 is restarted at the end.
set -e
BOX=$1
KO=${2:-dist/dm920eq.ko}
[ -n "$BOX" ] || { echo "usage: $0 <box-ip> [dm920eq.ko]"; exit 1; }
cd "$(dirname "$0")"
[ -f "$KO" ] || { echo "$KO not found - run ./build/build.sh or download it from the releases page"; exit 1; }

DEST=/usr/lib/enigma2/python/Plugins/Extensions/HardwareEQ
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp -r plugin/HardwareEQ "$TMP/"
cp "$KO" "$TMP/HardwareEQ/dm920eq.ko"

echo "checking box..."
ssh root@"$BOX" '[ "$(uname -r)" = "3.14-1.17-dm920" ]' || { echo "unsupported kernel on box (need 3.14-1.17-dm920)"; exit 1; }

echo "copying plugin..."
(cd "$TMP" && COPYFILE_DISABLE=1 tar czf - HardwareEQ) | ssh root@"$BOX" "
	set -e
	rmmod dm920eq 2>/dev/null || true
	rm -rf $DEST && mkdir -p $(dirname $DEST) && tar -C $(dirname $DEST) -xzf - 2>/dev/null
	chown -R root:root $DEST
	python -m py_compile $DEST/plugin.py $DEST/eqmath.py
	systemctl restart enigma2"
echo "done - open it with: blue button -> Erweiterungen -> Hardware-Equalizer"
