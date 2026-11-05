#!/usr/bin/env python3
"""Fetch pinned inputs and install the two ZLUDA components."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / 'zluda-deps.json').read_text())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def link(target, name):
    name.parent.mkdir(parents=True, exist_ok=True)
    name.unlink(missing_ok=True)
    name.symlink_to(target)


def notices(source, target):
    for path in source.rglob('*'):
        if path.is_file() and any(word in path.name.lower() for word in ('license', 'licence', 'copying', 'copyright', 'notice', 'provenance')):
            copy(path, target / path.relative_to(source))


def fetch(deps):
    downloads, sdk = deps / 'downloads', deps / 'sdk'
    downloads.mkdir(parents=True, exist_ok=True)
    sdk.mkdir(parents=True, exist_ok=True)
    for item in LOCK['downloads']:
        name = item.get('filename', item['url'].rsplit('/', 1)[-1])
        path = downloads / name
        if not path.is_file() or sha(path) != item['sha256']:
            temporary = path.with_name(path.name + '.part')
            subprocess.run(['curl', '--fail', '--location', '--retry', '3', '-o', str(temporary), item['url']], check=True)
            if sha(temporary) != item['sha256']:
                raise ValueError('download checksum mismatch: ' + name)
            temporary.replace(path)
        if name.endswith('.deb'):
            subprocess.run(['dpkg-deb', '-x', str(path), str(sdk)], check=True)


def native(deps, build, tool, source):
    library = tool / 'files/lib64'
    library.mkdir(parents=True, exist_ok=True)
    subprocess.run(['objcopy', '--strip-debug', str(build / 'target/release/libnvcuda.so'), str(library / 'libcuda.so.1')], check=True)
    subprocess.run(['objcopy', '--strip-debug', str(build / 'numactl-2.0.18/.libs/libnuma.so.1.0.0'), str(library / 'libnuma.so.1')], check=True)
    link('libcuda.so.1', library / 'libcuda.so')
    link('../lib/wine', library / 'wine')
    rocm = deps / ('sdk/opt/rocm-' + LOCK['rocm_version'])
    for name, aliases in LOCK['runtime'].items():
        copy(rocm / 'lib' / name, library / name, 0o755)
        for alias in aliases:
            link(name, library / alias)
    copy(deps / 'downloads/winetricks', tool / 'files/bin/winetricks', 0o755)
    notices(rocm / 'share/doc', tool / 'licenses/rocm')
    notices(deps / 'sdk/usr/share/doc', tool / 'licenses/rocm-packages')
    notices(build / 'numactl-2.0.18', tool / 'licenses/numactl')
    for name in ['LICENSE-APACHE', 'LICENSE-MIT', 'NOTICE', 'THIRD-PARTY-NOTICES.md']:
        if (source / name).is_file():
            copy(source / name, tool / 'licenses/zluda' / name)
    copy(HERE / 'zluda-deps.json', tool / 'licenses/zluda/dependencies.json')
    # Preserve dependency notices for statically linked Cargo/CMake components.
    metadata = json.loads(subprocess.check_output([str(deps / 'rust/bin/cargo'), 'metadata', '--locked', '--offline', '--format-version=1'], cwd=source))
    for package in metadata['packages']:
        root = Path(package['manifest_path']).parent
        for path in root.iterdir():
            if path.is_file() and any(word in path.name.lower() for word in ('license', 'copying', 'notice')):
                copy(path, tool / 'licenses/zluda-cargo' / (package['name'] + '-' + package['version']) / path.name)
    notices(source / 'ext/HiGHS', tool / 'licenses/HiGHS')
    notices(source / 'ext/llvm-project/llvm', tool / 'licenses/llvm')
    notices(source / 'ext/llvm-project/lld', tool / 'licenses/lld')


def stub_sources(source, expected):
    directory = source / 'dlls/nvcuda'
    paths = list(directory.rglob('*'))
    actual = {path.relative_to(source).as_posix(): sha(path) for path in paths if path.is_file()}
    if directory.is_symlink() or any(path.is_symlink() for path in paths) or actual != expected:
        raise ValueError('Wine CUDA stub sources differ from the pinned unmodified Valve tree')
    return actual


def bridge(build, tool, source, wine_source, wine_build):
    sys.path.insert(0, str(HERE.parent))
    import proton_zluda as policy
    cuda = tool / 'files/lib/wine-cuda'
    sources = stub_sources(wine_source, policy.VALVE_STUB_SOURCES)
    hashes = {}
    with tempfile.TemporaryDirectory(prefix='stock-stubs-', dir=build) as temporary:
        for arch in policy.ARCHES.values():
            original = tool / 'files/lib/wine' / arch / 'nvcuda.dll'
            own = wine_build / ('dst-wine-' + arch.split('-')[0]) / 'lib/wine' / arch / 'nvcuda.dll'
            if own.is_symlink() or not own.is_file():
                raise ValueError('Wine CUDA stubs must be regular files from this build')
            expected = Path(temporary) / (arch + '.dll')
            subprocess.run(['objcopy', '-p', '--set-section-flags', '.text=contents,alloc,load,readonly,code',
                            '--strip-debug', str(own), str(expected)], check=True)
            hashes[arch] = sha(expected)
            if original.is_symlink() or (original.exists() and (not original.is_file() or sha(original) != hashes[arch])):
                raise ValueError('installed CUDA stub differs from this build\'s Wine output: ' + arch)
            copy(expected, cuda / 'stub' / arch / 'nvcuda.dll')
    record = dict(proton_pin=policy.VALVE_PROTON_PIN, wine_pin=policy.VALVE_WINE_PIN,
                  source_sha256=sources, sha256=hashes)
    print('Stock stub source/build receipt: ' + json.dumps(record), flush=True)
    (cuda / 'stubs.json').write_text(json.dumps(record, indent=2) + '\n')
    artifacts = build / 'artifacts'
    subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=artifacts, check=True)
    for arch, name in [('x86_64-windows', 'nvcuda.dll'), ('i386-windows', 'nvcuda.dll'), ('x86_64-unix', 'nvcuda.so')]:
        copy(artifacts / arch / name, cuda / 'zluda' / arch / name, 0o755 if name.endswith('.so') else 0o644)
    for path in (tool / 'files/lib/wine').rglob('*'):
        if path.name.lower() in {'nvcuda.dll', 'nvcuda.so', 'nvcuda.dll.so', 'libcuda.so', 'libcuda.so.1'}:
            path.unlink()
    notices(source, tool / 'licenses/zluda-wine')


if __name__ == '__main__':
    command, *args = sys.argv[1:]
    {'fetch': fetch, 'native': native, 'bridge': bridge}[command](*map(Path, args))
