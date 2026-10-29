#!/usr/bin/env bash
# Run inside the pinned SDK. Validate small native dependencies before LLVM.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${SDK_ROOT:?}"
jobs=${JOBS:-3}
[[ $jobs =~ ^[1-3]$ ]] || { echo 'JOBS must be 1..3' >&2; exit 2; }
export CARGO_BUILD_JOBS=$jobs CMAKE_BUILD_PARALLEL_LEVEL=$jobs
here=$(cd -- "$(dirname -- "$0")" && pwd)
unset DESTDIR
export ZLUDA_REAL_CMAKE
ZLUDA_REAL_CMAKE=$(command -v cmake)
export CMAKE=$here/native-cmake.py
mkdir -p "$BUILD_ROOT/native/log"
cd "$SOURCE_ROOT/zluda"
cargo fetch --locked 2>&1 | tee "$BUILD_ROOT/native/log/fetch.log"
# These roots exercise HiGHS, all ROCm bindings with cuda_types' real feature
# selections (notably hipfft without native linking), bundled SQLite, LZ4,
# dark_api's Zstd/zlib-rs, BLAKE3 and vergen-gix's build dependencies. None
# depends on llvm_zluda.
cargo build --offline --locked --release -vv \
    -p highs -p cuda_types -p dark_api -p zluda_cache -p lz4-sys -p blake3 -p vergen-gix \
    2>&1 | tee "$BUILD_ROOT/native/log/prerequisites.log"
python3 "$here/native_audit.py" "$SOURCE_ROOT/zluda/target/release" "$SDK_ROOT" \
    "$BUILD_ROOT/native/link-layout.json"
# Keep LLVM in its own invocation. A sibling small crate cannot fail while its
# long-running build script continues for another hour.
cargo build --offline --locked --release -vv -p llvm_zluda \
    2>&1 | tee "$BUILD_ROOT/native/log/llvm.log"
cargo build --offline --locked --release -vv -p zluda \
    2>&1 | tee "$BUILD_ROOT/native/log/zluda.log"
