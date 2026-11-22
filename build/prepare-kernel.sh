#!/bin/sh
# Runs inside the build container. Downloads and prepares the DM920 kernel tree in /src
# (Linux 3.14 + stable patch 3.14.79 + Dream Multimedia patch + dm920 defconfig).
set -e

SRC=/src
TREE=$SRC/linux-3.14
DL=$SRC/downloads
STAMP=$TREE/.dm920-prepared

KERNEL_URL=https://cdn.kernel.org/pub/linux/kernel/v3.x/linux-3.14.tar.xz
KERNEL_SHA=61558aa490855f42b6340d1a1596be47454909629327c49a5e4e10268065dffa
STABLE_URL=https://cdn.kernel.org/pub/linux/kernel/v3.x/patch-3.14.79.xz
STABLE_SHA=b391b76f3a5c6c8cf7234f8c01821b88584ddf90f45323e09c126c5e7624b12c
DREAM_FILE=linux-dreambox-3.14-6fa88d2001194cbff63ad94cb713b6cd5ea02739.patch.xz
DREAM_URL=https://raw.githubusercontent.com/oe-mirrors/dreambox/master/$DREAM_FILE
DREAM_SHA=85a18df9f07e221c0fd305cc213e5557d9006a40b3229bf9d13e5bc9ba8e2371
DEFCONFIG_URL=https://raw.githubusercontent.com/wzcwzc/meta-dream/c45314a40690d9125245fc47be5d576696320d1d/recipes-bsp/linux/linux-dreambox-3.14/dm920/defconfig
DEFCONFIG_SHA=b1a6f659e0d030eff334c078262e0d92441d9f85d9c518f2bc6ba41df11311fb

if [ -f "$STAMP" ]; then
	echo "kernel tree already prepared"
	exit 0
fi

fetch() { # url sha file
	if [ ! -f "$DL/$3" ]; then
		echo "downloading $3"
		wget -q -O "$DL/$3.part" "$1"
		mv "$DL/$3.part" "$DL/$3"
	fi
	echo "$2  $DL/$3" | sha256sum -c - >/dev/null || { echo "checksum mismatch: $3"; rm -f "$DL/$3"; exit 1; }
}

mkdir -p "$DL"
fetch $KERNEL_URL $KERNEL_SHA linux-3.14.tar.xz
fetch $STABLE_URL $STABLE_SHA patch-3.14.79.xz
fetch $DREAM_URL $DREAM_SHA $DREAM_FILE
fetch $DEFCONFIG_URL $DEFCONFIG_SHA defconfig-dm920

echo "unpacking and patching"
rm -rf "$TREE"
tar -C "$SRC" -xJf "$DL/linux-3.14.tar.xz"
cd "$TREE"
xzcat "$DL/patch-3.14.79.xz" | patch -p1 -s
xzcat "$DL/$DREAM_FILE" | patch -p1 -s
# The Dreambox kernel reports itself as 3.14-1.17-dm920 (no sublevel)
sed -e "/^SUBLEVEL = /d" -i Makefile
cp "$DL/defconfig-dm920" .config

echo "configuring"
make -s ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- olddefconfig
make -s ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- modules_prepare
REL=$(make -s ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- kernelrelease)
[ "$REL" = "3.14-1.17-dm920" ] || { echo "unexpected kernel release $REL"; exit 1; }
touch "$STAMP"
echo "kernel tree ready: $REL"
