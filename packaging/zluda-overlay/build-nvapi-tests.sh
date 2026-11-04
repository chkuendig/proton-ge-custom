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
python3 - "$qsrc" "$BUILD_ROOT/nvapi-fixtures/COM-MOCK-ADAPTATION.json" <<'MOCKS'
import hashlib, json, re, sys
from pathlib import Path
root = Path(sys.argv[1]) / 'tests/mocks'
records = {}
for name, expected in [('dxgi_mocks.h', 65), ('d3d_mocks.h', 11),
                       ('d3d11_mocks.h', 183), ('d3d12_mocks.h', 197)]:
    path = root / name
    before = path.read_text()
    after, count = re.subn(r'\b(MAKE|IMPLEMENT)_MOCK(?=\d+\()', r'\1_STDMETHOD_MOCK', before)
    if count != expected:
        raise ValueError('COM mock source changed: ' + name + ': ' + str(count))
    path.write_text(after)
    digest = lambda data: hashlib.sha256(data.encode()).hexdigest()
    records[name] = {'source_sha256': digest(before), 'adapted_sha256': digest(after), 'methods': count}
Path(sys.argv[2]).write_text(json.dumps({'scope': 'test-only COM mocks; use vendored stdcall macros',
                                       'files': records}, indent=2) + '\n')
MOCKS
for bits in 32 64; do
    build=$BUILD_ROOT/nvapi-tests-$bits
    meson setup "$build" "$qsrc" --cross-file "$qsrc/build-win$bits.txt" \
        --buildtype release -Denable_tests=true -Dcpp_args=-fmax-errors=8
    name=nvapi-tests.exe
    [[ $bits != 64 ]] || name=nvapi64-tests.exe
    ninja -C "$build" -j"$jobs" "tests/$name"
    install -m644 "$build/tests/$name" "$BUILD_ROOT/nvapi-fixtures/nvapi-tests$bits.exe"
done
