#!/usr/bin/env python3
"""Exercise installed Proton setup; replay its game past the Steam handshake."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
tool, obj = [Path(arg).resolve() for arg in sys.argv[1:]]
receipt = obj / 'launcher-smoke.json'
with tempfile.TemporaryDirectory(prefix='zluda-launcher-', dir=obj) as temporary:
    root = Path(temporary)
    private = root / 'tool'
    subprocess.run(['cp', '--reflink=auto', '-a', str(tool), str(private)], check=True)
    sys.path.insert(0, str(HERE.parents[1] / "make"))
    import zluda as checks
    component = HERE.parents[1] / 'zluda-wine/wine/zluda_wine'
    rows = [line.split('\t') for line in (component / 'api/wow64-backend-abi.tsv').read_text().splitlines() if line and not line.startswith('#')]
    (root / 'wow64-native-contract.h').write_text(''.join(f'int {name}({args});\n' for name, args in rows))
    backend = private / 'files/lib64/libcuda.so.1'
    backend.unlink()
    subprocess.run(['cc', '-O2', '-shared', '-fPIC', '-pthread', '-I', str(root), str(component / 'tests/wow64/backend.c'), '-o', str(backend)], check=True)
    probe = root / 'game/probe.exe'
    for name in ['game', 'home', 'steam', 'compatdata', 'runtime', 'logs']:
        (root / name).mkdir(mode=0o700)
    subprocess.run(['x86_64-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-static-libgcc', str(HERE / 'probe.c'), '-o', str(probe)], check=True)
    # Model Steam's external video provider using this build's FFmpeg only.
    for arch, steam_arch in [('x86_64', 'ubuntu12_64'), ('i386', 'ubuntu12_32')]:
        video = root / 'steam' / steam_arch / 'video'
        video.mkdir(parents=True)
        for path in (obj / ('dst-ffmpeg-' + arch)).rglob('*.so*'):
            if path.is_file():
                (video / path.name).symlink_to(path.resolve())
    preloader = private / 'files/lib/wine/x86_64-unix/wine-preloader'
    original_sha = hashlib.sha256(preloader.read_bytes()).hexdigest()
    preloader.rename(preloader.with_name('wine-preloader-uncaptured'))
    preloader.write_text('#!/usr/bin/python3\n' + r'''
import json, os, pathlib, sys
path = pathlib.Path(__file__)
argv = list(sys.argv)
if len(argv) >= 4 and argv[2] == r'c:\windows\system32\steam.exe':
    if ([pathlib.Path(arg) for arg in argv[:2]] != [path, path.with_name('wine')] or
            os.environ.get('WINELOADERNOEXEC') != '1'):
        raise ValueError('unexpected Proton preloader dispatch')
    replay = argv[:2] + argv[3:]
    keys = ['WINEDLLPATH', 'WINEDLLOVERRIDES', 'LD_LIBRARY_PATH', 'WINEPREFIX',
            'WINEARCH', 'WINELOADERNOEXEC', 'PROTON_ENABLE_ZLUDA', 'ZLUDA_WINE_BACKEND_PATH']
    record = dict(original_argv=argv, replay_argv=replay, cwd=os.getcwd(),
                  environment={key: os.environ.get(key) for key in keys},
                  replacement={'removed': argv[2], 'target': argv[3]},
                  steam_client_handshake='pending Deck qualification')
    pathlib.Path(os.environ['ZLUDA_SMOKE_RECEIPT']).write_text(json.dumps(record, indent=2) + '\n')
    argv = replay
os.execv(str(path.with_name('wine-preloader-uncaptured')), argv)
''')
    preloader.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('WINE', 'PROTON_', 'ZLUDA_', 'STEAM_'))
           and key not in ('SteamAppId', 'SteamGameId', 'LD_PRELOAD', 'LD_LIBRARY_PATH')}
    env.update(HOME=str(root / 'home'), XDG_RUNTIME_DIR=str(root / 'runtime'),
               STEAM_COMPAT_DATA_PATH=str(root / 'compatdata'),
               STEAM_COMPAT_CLIENT_INSTALL_PATH=str(root / 'steam'),
               STEAM_COMPAT_INSTALL_PATH=str(root / 'game'),
               STEAM_COMPAT_APP_ID='999999', SteamAppId='999999', SteamGameId='999999',
               PROTON_ENABLE_ZLUDA='1', PROTON_NO_FSYNC='1', PROTON_USE_WINED3D='1',
               PROTON_LOG='1', PROTON_LOG_DIR=str(root / 'logs'), WINEDEBUG='-all,err+all',
               ZLUDA_SMOKE_RECEIPT=str(receipt))
    target = 'Z:' + str(probe).replace('/', '\\')
    try:
        subprocess.run([str(private / 'proton'), 'waitforexitandrun', target, 'on'], cwd=root / 'game', env=env, check=True, timeout=240)
    finally:
        log = root / 'logs/steam-999999.log'
        if log.exists():
            print(log.read_text(errors='replace'), flush=True)
        cleanup = dict(env, WINEPREFIX=str(root / 'compatdata/pfx'))
        subprocess.run([str(private / 'files/bin/wineserver'), '-k'], env=cleanup, timeout=30)
    record = json.loads(receipt.read_text())
    command, active = record['original_argv'], record['environment']
    assert command[2:] == [r'c:\windows\system32\steam.exe', target, 'on'], record
    assert record['replay_argv'] == command[:2] + command[3:], record
    assert record['cwd'] == str(root / 'game'), record
    assert Path(active['WINEPREFIX']) == root / 'compatdata/pfx', record
    assert active['WINEDLLPATH'].split(':')[0] == str(private / 'files/lib/wine-cuda/zluda'), record
    assert sum('/wine-cuda/' in entry for entry in active['WINEDLLPATH'].split(':')) == 1, record
    assert active['ZLUDA_WINE_BACKEND_PATH'] == str(backend), record
    assert active['LD_LIBRARY_PATH'].split(':')[:2] == [str(private / 'files/lib64/wine/x86_64-unix'), str(private / 'files/lib64')], record
    assert 'nvcuda=b' in active['WINEDLLOVERRIDES'].split(';'), record
    checks.validate_default_prefix(private, root / 'compatdata/pfx')
    for directory in checks.ARCHES:
        stub = root / 'compatdata/pfx/drive_c/windows' / directory / 'nvcuda.dll'
        assert stub.stat().st_ino != (private / 'files/share/default_pfx/drive_c/windows' / directory / 'nvcuda.dll').stat().st_ino
    assert 'PASS: PE64 on CUDA module' in log.read_text(), log
    record.update(status='passed', preloader_sha256=original_sha,
                  installed_policy=str(private / 'proton'), verb='waitforexitandrun')
    receipt.write_text(json.dumps(record, indent=2) + '\n')
assert not list(tool.rglob('__pycache__')), 'Python bytecode cache in the redistributable'
print('PASS: installed Proton Session, prefix setup, ZLUDA selection and recorded preloader replay')
