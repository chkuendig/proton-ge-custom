#!/usr/bin/env bash
# Compile both mock-test architectures before the expensive Valve build.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}"
jobs=${JOBS:-3}
[[ $jobs =~ ^[1-3]$ ]]
here=$(cd -- "$(dirname -- "$0")" && pwd)
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
interface = Path(sys.argv[1]) / 'src/interfaces/vkd3d-proton_interfaces.h'
ordinary = {name for prefix, name in re.findall(
    r'\bvirtual\s+([^;=]+?)\s+(\w+)\s*\([^;]*?\)\s*=\s*0\s*;', interface.read_text())
    if 'STDMETHODCALLTYPE' not in prefix}
if len(ordinary) != 11:
    raise ValueError('review the pinned native C++ extension method signatures')
records = {}
for name, expected in [('dxgi_mocks.h', 65), ('d3d_mocks.h', 11),
                       ('d3d11_mocks.h', 183), ('d3d12_mocks.h', 197)]:
    path = root / name
    before = path.read_text()
    preserved = []
    def adapt(match):
        if name == 'd3d12_mocks.h' and match[3] in ordinary:
            preserved.append(match[3])
            return match[0]
        return match[1] + '_STDMETHOD_MOCK' + match[2] + '(' + match[3]
    after, count = re.subn(r'\b(MAKE|IMPLEMENT)_MOCK(\d+)\((\w+)', adapt, before)
    if count != expected:
        raise ValueError('COM mock source changed: ' + name + ': ' + str(count))
    path.write_text(after)
    digest = lambda data: hashlib.sha256(data.encode()).hexdigest()
    if name == 'd3d12_mocks.h' and (set(preserved) != ordinary or len(preserved) != 11):
        raise ValueError('native C++ mock method inventory changed')
    records[name] = {'source_sha256': digest(before), 'adapted_sha256': digest(after), 'methods': count,
                     'stdcall_methods': count - len(preserved), 'native_cpp_methods': preserved}
Path(sys.argv[2]).write_text(json.dumps({'scope': 'test-only COM mocks; use vendored stdcall macros',
                                       'native_cpp_interface_sha256': hashlib.sha256(interface.read_bytes()).hexdigest(),
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
