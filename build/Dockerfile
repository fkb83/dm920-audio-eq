# Build environment for the dm920eq kernel module.
# The DM920 kernel (3.14-1.17-dm920) was built with GCC 5.3, so GCC 5 is used here as well.
FROM ubuntu:16.04
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      gcc-5-arm-linux-gnueabihf binutils-arm-linux-gnueabihf \
      make bc xz-utils patch gcc libc6-dev wget ca-certificates python3 python3-numpy >/dev/null \
 && ln -s /usr/bin/arm-linux-gnueabihf-gcc-5 /usr/bin/arm-linux-gnueabihf-gcc \
 && rm -rf /var/lib/apt/lists/*
