#!/usr/bin/env bash
# CI-only external Steam providers. Never install these into the Proton tool.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${FIXTURE_ROOT:?}" "${SDK_IMAGE:?}"
[[ ${CI:-} == true && ${JOBS:-3} =~ ^[1-3]$ ]]
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
source = Path(os.environ['SOURCE_ROOT'])
lock = json.loads((source / 'packaging/zluda-overlay/inputs.lock.json').read_text())
def git(*args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
assert os.environ['SDK_IMAGE'] == lock['sdk_image'], 'fixture SDK differs from lock'
assert git('rev-parse', 'HEAD:ffmpeg') == lock['components']['ffmpeg']
assert git('-C', 'ffmpeg', 'rev-parse', 'HEAD') == lock['components']['ffmpeg']
assert not git('-C', 'ffmpeg', 'status', '--porcelain', '--untracked-files=all')
assert not Path(os.environ['FIXTURE_ROOT']).exists(), 'fixture must be fresh'
PY
mkdir -p "$BUILD_ROOT" "$FIXTURE_ROOT/steamrtarm64/video"
# The SDK compiler wrappers must not use their default cache under /root.
export CCACHE_DIR=$BUILD_ROOT/ccache
flags=(--enable-shared --disable-static --disable-everything --disable-programs
       --disable-doc --disable-inline-asm --enable-demuxer=matroska)
for entry in x86_64:64 i686:32; do
    machine=${entry%:*}; bits=${entry#*:}; arch=$machine
    [[ $bits != 32 ]] || arch=x86
    mkdir -p "$BUILD_ROOT/$machine"
    (
        cd "$BUILD_ROOT/$machine"
        if ! "$SOURCE_ROOT/ffmpeg/configure" "${flags[@]}" --cross-prefix="$machine-linux-gnu-" \
            --arch="$arch" --target-os=linux --prefix="$FIXTURE_ROOT/ubuntu12_$bits" \
            --libdir="$FIXTURE_ROOT/ubuntu12_$bits/video"; then
            tail -n 80 ffbuild/config.log
            exit 1
        fi
        make -j"${JOBS:-3}"
        make install-libs
    )
done
python3 - <<'PY'
import hashlib, json, os, struct
from pathlib import Path
source, root = [Path(os.environ[key]) for key in ['SOURCE_ROOT', 'FIXTURE_ROOT']]
lock = json.loads((source / 'packaging/zluda-overlay/inputs.lock.json').read_text())
files = {}
for bits, machine in [(64, 62), (32, 3)]:
    directory = root / f'ubuntu12_{bits}/video'
    for name in ['libavutil.so.56', 'libavformat.so.58', 'libavcodec.so.58']:
        data = (directory / name).read_bytes()
        assert data[:4] == b'\x7fELF' and struct.unpack_from('<H', data, 18)[0] == machine
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            assert path.resolve().is_relative_to(directory.resolve())
            files[str(path.relative_to(root))] = {'link': os.readlink(path)}
        elif path.is_file():
            files[str(path.relative_to(root))] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
record = dict(ci_only=True, bundled_in_product=False,
    go_ref='e1c6a6e3-20261104-valve-ffmpeg-ci', source=lock['source_repositories']['ffmpeg'],
    commit=lock['components']['ffmpeg'], sdk_image=lock['sdk_image'], files=files)
(root / 'CI-STEAM-VIDEO.json').write_text(json.dumps(record, indent=2) + '\n')
PY
