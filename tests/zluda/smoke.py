#!/usr/bin/env python3
"""Check the assembled tool and run both PE widths against a CPU fixture."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
tool, obj = [Path(arg).resolve() for arg in sys.argv[1:]]
assert not (tool / 'proton_zluda.py').exists()
sys.path.insert(0, str(HERE.parents[1] / "make"))
import zluda as checks

cuda, record = checks.stub_assets(tool)
checks.validate_default_prefix(tool, tool / 'files/share/default_pfx')
for path in (tool / 'files/lib/wine').rglob('*'):
    assert path.name.lower() not in {'nvcuda.dll', 'nvcuda.so', 'nvcuda.dll.so', 'libcuda.so', 'libcuda.so.1'}, path
# Valve intentionally leaves its Steam-provided FFmpeg unbundled. Use the
# exact libraries already built by this make, outside the redistributable.
library_dirs = [tool / name for name in ['files/lib64', 'files/lib/wine/x86_64-unix', 'files/lib/x86_64-linux-gnu']]
library_dirs += sorted((obj / 'dst-ffmpeg-x86_64').glob('lib/*'))
env = dict(os.environ, LD_LIBRARY_PATH=':'.join(map(str, library_dirs)))
for directory in ['files/lib64', 'files/lib/wine/x86_64-unix', 'files/lib/wine-cuda/zluda/x86_64-unix']:
    for path in (tool / directory).iterdir():
        if not path.is_file() or path.is_symlink() or path.open('rb').read(4) != b'\x7fELF':
            continue
        dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
        if '(NEEDED)' not in dynamic:
            continue
        output = subprocess.check_output(['ldd', str(path)], env=env, text=True)
        assert 'not found' not in output, (path, output)
        if path.name == 'nvcuda.so':
            assert any(line.strip().startswith('ntdll.so => ' + str(tool / 'files/lib/wine/x86_64-unix/ntdll.so')) for line in output.splitlines()), output
            symbols = subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(path)], text=True)
            assert '__wine_unix_call_funcs' in symbols and '__wine_unix_call_wow64_funcs' in symbols
            assert 'BIND_NOW' in dynamic or 'NOW' in dynamic
subprocess.run([sys.executable, '-c',
                'import ctypes, sys; b=ctypes.CDLL(sys.argv[1]); v=ctypes.c_int(); '
                'assert b.cuDriverGetVersion(ctypes.byref(v)) == 0 and v.value > 0; '
                'print("PASS: native CUDA driver version", v.value)',
                str(tool / 'files/lib64/libcuda.so.1')], env=env, check=True)
with tempfile.TemporaryDirectory(prefix='zluda-smoke-', dir=obj) as temporary:
    root = Path(temporary)
    backend = root / 'libcuda-fixture.so'
    component = HERE.parents[1] / 'zluda-wine/wine/zluda_wine'
    rows = [line.split('\t') for line in (component / 'api/wow64-backend-abi.tsv').read_text().splitlines() if line and not line.startswith('#')]
    (root / 'wow64-native-contract.h').write_text(''.join(f'int {name}({args});\n' for name, args in rows))
    subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-pthread', '-I', str(root), str(component / 'tests/wow64/backend.c'), '-o', str(backend)], check=True)
    for triplet, arch, module_arch in [('x86_64', 'win64', 'x86_64-windows'), ('i686', 'wow64', 'i386-windows')]:
        subprocess.run(['python3', str(component / 'tools/pe_artifact.py'), '--triplet', triplet, str(cuda / 'zluda' / module_arch / 'nvcuda.dll')], check=True)
        probe = root / ('probe-' + triplet + '.exe')
        subprocess.run([triplet + '-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-static-libgcc', str(HERE / 'probe.c'), '-o', str(probe)], check=True)
        prefix = root / arch
        shutil.copytree(tool / 'files/share/default_pfx', prefix, symlinks=False)
        # Proton restores the DOS drive links stripped from its template.
        drives = prefix / 'dosdevices'
        drives.mkdir(exist_ok=True)
        (drives / 'c:').symlink_to('../drive_c')
        (drives / 'z:').symlink_to('/')
        base = dict(env, WINEPREFIX=str(prefix), WINEARCH=arch, WINEDEBUG='-all', WINEDLLOVERRIDES='nvcuda=b', WINEDLLPATH=str(cuda / 'stub'))
        try:
            for mode in ['off', 'on', 'off']:
                active = dict(base, PROTON_ENABLE_ZLUDA='1' if mode == 'on' else '0', WINEDLLPATH=str(cuda / ('zluda' if mode == 'on' else 'stub')))
                if mode == 'on':
                    active['ZLUDA_WINE_BACKEND_PATH'] = str(backend)
                subprocess.run([str(tool / 'files/bin/wine'), str(probe), mode], env=active, check=True, timeout=120)
                subprocess.run([str(tool / 'files/bin/wineserver'), '-w'], env=active, check=True, timeout=30)
        finally:
            subprocess.run([str(tool / 'files/bin/wineserver'), '-k'], env=base, timeout=30)
print('PASS: complete tool, ELF closure, stock stubs, PE64 and WoW64 off/on/off')
