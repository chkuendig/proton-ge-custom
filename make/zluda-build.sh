#!/usr/bin/env bash
set -euo pipefail
mode=${1:?}; deps=${2:?}
here=$(cd -- "$(dirname -- "$0")" && pwd)
unset DESTDIR
export CARGO_BUILD_JOBS=3 CMAKE_BUILD_PARALLEL_LEVEL=3
export PATH="$deps/rust/bin:$PATH"

if [[ $mode == deps ]]; then
    mkdir -p "$deps/install"
    for package in rust-1.99.0-x86_64-unknown-linux-gnu rust-std-1.99.0-x86_64-pc-windows-gnu rust-std-1.99.0-i686-pc-windows-gnu; do
        [[ ! -f $deps/install/$package.done ]] || continue
        tar -xf "$deps/downloads/$package.tar.xz" -C "$deps/install"
        args=(--prefix="$deps/rust" --disable-ldconfig)
        if [[ $package == rust-1* ]]; then
            args+=(--components=rustc,cargo,rust-std-x86_64-unknown-linux-gnu)
        fi
        "$deps/install/$package/install.sh" "${args[@]}"
        rm -rf "$deps/install/$package"
        touch "$deps/install/$package.done"
    done
    exit
fi

[[ $mode == native ]]
source=${3:?}; build=${4:?}
mkdir -p "$build/target" "$build/stubs"
export CARGO_TARGET_DIR=$build/target
export ZLUDA_REAL_CMAKE
ZLUDA_REAL_CMAKE=$(command -v cmake)
export CMAKE=$here/zluda-cmake.py ZLUDA_BUILD_SOURCE=$source
export ZLUDA_MATHCOMPAT_OBJECT=$build/mathcompat.o
cc -O2 -fPIC -fno-builtin -c "$here/zluda-math-compat.c" -o "$ZLUDA_MATHCOMPAT_OBJECT"
for name in rocsparse MIOpen hipblaslt rocblas; do
    printf '\n' | cc -shared -x c - -o "$build/stubs/lib$name.so"
done
export LIBRARY_PATH="$build/stubs:$ZLUDA_HIP_SDK_ROOT/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
export LD_LIBRARY_PATH="$ZLUDA_HIP_SDK_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$source"
cargo fetch --locked
# Keep prerequisite failures separate from the long LLVM build.
cargo build --locked --offline --release -p highs-sys --features highs-sys/highs_release
cargo build --locked --offline --release -p llvm_zluda
cargo build --locked --offline --release -p zluda

tar -xf "$deps/downloads/numactl-2.0.18.tar.gz" -C "$build"
cd "$build/numactl-2.0.18"
./configure --prefix=/usr --libdir=/usr/lib --disable-static
make -j3 libnuma.la
