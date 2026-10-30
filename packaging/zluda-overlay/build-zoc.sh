#!/usr/bin/env bash
# CI diagnostic build only; execute after the backend/LLVM stage in the SDK.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}"
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
# Retain a compiler-only capsule for separately cleared local stack measurement.
# No PTX input is used or retained by CI, and zoc is not executed here.
cargo build --release --locked --offline -p compiler -j"$jobs" \
    2>&1 | tee "$BUILD_ROOT/native/log/zoc.log"
STACK_PROBE_BUILD_ROOT=$BUILD_ROOT/stack-probe bash "$SOURCE_ROOT/zluda/wine/zluda_wine/tests/stack/build-probe.sh"
python3 "$here/zoc_capsule.py" --source "$SOURCE_ROOT/zluda" \
    --binary "$SOURCE_ROOT/zluda/target/release/zoc" --dest "$BUILD_ROOT/zoc-capsule" --stack-probe-build "$BUILD_ROOT/stack-probe"
