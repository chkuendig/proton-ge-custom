#!/usr/bin/env bash
# Compile both mock-test architectures before the expensive Valve build.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}"
jobs=${JOBS:-3}
[[ $jobs =~ ^[1-3]$ ]]
here=$SOURCE_ROOT/packaging/zluda-overlay
prepared=$BUILD_ROOT/patched/dxvk-nvapi
if [[ ! -d $prepared ]]; then
    python3 "$here/component_patches.py" prepare "$SOURCE_ROOT" "$prepared" dxvk-nvapi
fi
qsrc=$BUILD_ROOT/nvapi-qualification
[[ ! -e $qsrc ]]
cp -a "$prepared" "$qsrc"
python3 - "$here" <<'CHECK'
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from inputs import LOCK, HERE, sha256
record = LOCK['nvapi_qualification']
if sha256(HERE.parents[1] / record['path']) != record['sha256']:
    raise ValueError('NVAPI qualification patch checksum mismatch')
CHECK
patch -d "$qsrc" -p1 < "$here/nvapi-policy-tests.patch"
mkdir -p "$BUILD_ROOT/nvapi-fixtures"
for bits in 32 64; do
    build=$BUILD_ROOT/nvapi-tests-$bits
    meson setup "$build" "$qsrc" --cross-file "$qsrc/build-win$bits.txt" \
        --buildtype release -Denable_tests=true
    name=nvapi-tests.exe
    [[ $bits != 64 ]] || name=nvapi64-tests.exe
    ninja -C "$build" -j"$jobs" "tests/$name"
    install -m644 "$build/tests/$name" "$BUILD_ROOT/nvapi-fixtures/nvapi-tests$bits.exe"
done
