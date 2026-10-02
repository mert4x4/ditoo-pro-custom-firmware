#!/bin/sh
# Build the nds32le-elf cross compiler (GNU binutils 2.40 + GCC 14.2, C only, bare metal).
# Only needed to change remote/remote.c; installing the firmware does not need it.
#
#   firmware/tools/build_toolchain.sh [prefix]      (default ~/.local/nds32)
#
# Debian/Ubuntu packages: sudo apt install build-essential curl xz-utils texinfo \
#     libgmp-dev libmpfr-dev libmpc-dev libisl-dev zlib1g-dev
# Takes 15-40 minutes and about 3 GB of disk while building.
set -e
PREFIX=${1:-$HOME/.local/nds32}
WORK=${WORK:-$(mktemp -d)}
JOBS=$(nproc 2>/dev/null || echo 4)
cd "$WORK"

curl -fLO https://ftp.gnu.org/gnu/binutils/binutils-2.40.tar.xz
curl -fLO https://ftp.gnu.org/gnu/gcc/gcc-14.2.0/gcc-14.2.0.tar.xz
tar xf binutils-2.40.tar.xz
tar xf gcc-14.2.0.tar.xz

mkdir -p bb && cd bb
../binutils-2.40/configure --target=nds32le-elf --prefix="$PREFIX" --disable-nls --disable-werror \
    --with-system-zlib MAKEINFO=true
make -j"$JOBS" MAKEINFO=true
make install MAKEINFO=true
cd ..

export PATH="$PREFIX/bin:$PATH"
mkdir -p gb && cd gb
../gcc-14.2.0/configure --target=nds32le-elf --prefix="$PREFIX" --enable-languages=c \
    --without-headers --with-newlib --disable-nls --disable-shared --disable-threads \
    --disable-libssp --disable-libquadmath --disable-libgomp --disable-multilib
make -j"$JOBS" all-gcc all-target-libgcc
make install-gcc install-target-libgcc

echo "installed in $PREFIX/bin (build files left in $WORK)"
