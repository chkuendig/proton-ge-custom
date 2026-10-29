#!/usr/bin/env bash
# Install verified archives into the private build prefix, never system paths.
set -euo pipefail
: "${BUILD_ROOT:?}" "${DOWNLOAD_ROOT:?}"
version=1.99.0
mkdir -p "$BUILD_ROOT/rust-installer"
if [[ ! -x $BUILD_ROOT/rust/bin/rustc ]]; then
    tar -xf "$DOWNLOAD_ROOT/rust-$version-x86_64-unknown-linux-gnu.tar.xz" -C "$BUILD_ROOT/rust-installer"
    "$BUILD_ROOT/rust-installer/rust-$version-x86_64-unknown-linux-gnu/install.sh" \
        --prefix="$BUILD_ROOT/rust" --without=rust-docs --disable-ldconfig
fi
if [[ ${BUILD_PART:-all} != zluda ]]; then
    for target in x86_64-pc-windows-gnu i686-pc-windows-gnu; do
        tar -xf "$DOWNLOAD_ROOT/rust-std-$version-$target.tar.xz" -C "$BUILD_ROOT/rust-installer"
        "$BUILD_ROOT/rust-installer/rust-std-$version-$target/install.sh" \
            --prefix="$BUILD_ROOT/rust" --disable-ldconfig
    done
fi
[[ $("$BUILD_ROOT/rust/bin/rustc" --version) == 'rustc 1.99.0 '* ]]
