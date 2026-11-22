#!/bin/sh
# Builds dist/dm920eq.ko with Docker. Usage: ./build/build.sh
set -e
cd "$(dirname "$0")/.."

IMAGE=dm920-audio-eq-build
VOLUME=dm920-audio-eq-kernel

docker build -q -t $IMAGE build >/dev/null
docker volume create $VOLUME >/dev/null
docker run --rm -v $VOLUME:/src -v "$PWD":/repo $IMAGE sh /repo/build/prepare-kernel.sh
mkdir -p dist
docker run --rm -v $VOLUME:/src -v "$PWD":/repo $IMAGE sh -c '
	set -e
	cp -r /repo/kernel /tmp/kernel
	make -s -C /src/linux-3.14 ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- M=/tmp/kernel modules 2>&1 \
		| grep -v -e "Module.symvers" -e "modules will have no dependencies" || true
	cp /tmp/kernel/dm920eq.ko /repo/dist/
	arm-linux-gnueabihf-readelf -p .modinfo /repo/dist/dm920eq.ko | grep vermagic'
echo "built dist/dm920eq.ko"
