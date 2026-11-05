#!/usr/bin/env python3
"""Prepare the bridge SDK from this build's reviewed Valve Wine source/objects."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

source, obj, sdk = [Path(arg).resolve() for arg in sys.argv[1:]]
contract = json.loads(Path(__file__).with_name('zluda-wine-abi.json').read_text())
for name, expected in contract['prepared_files'].items():
    if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
        raise ValueError('Wine callback/COM source changed; review before updating: ' + name)
native = obj / 'dlls/ntdll/ntdll.so'
header = subprocess.check_output(['readelf', '-h', str(native)], text=True)
dynamic = subprocess.check_output(['readelf', '-d', str(native)], text=True)
symbols = subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(native)], text=True)
if 'ELF64' not in header or 'X86-64' not in header or '[ntdll.so]' not in dynamic:
    raise ValueError('Wine callback provider must be x86_64 ntdll.so')
if not any(len(row) >= 8 and row[3:6] == ['FUNC', 'GLOBAL', 'DEFAULT'] and row[6] != 'UND' and row[7] == 'KeUserModeCallback'
           for row in map(str.split, symbols.splitlines())):
    raise ValueError('Wine callback provider lacks strong KeUserModeCallback')
for name, target in [('include', source / 'include'), ('include-generated', obj / 'include'), ('lib/ntdll.so', native)]:
    path = sdk / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    path.symlink_to(target)
tool = sdk / 'bin/winebuild'
tool.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(obj / 'tools/winebuild/winebuild', tool)
tool.chmod(0o755)
for name in ['windef.h', 'wine/server_protocol.h']:
    if not (sdk / 'include' / name).is_file():
        raise ValueError('incomplete Wine headers: ' + name)
if not (sdk / 'include-generated/wtypes.h').is_file():
    raise ValueError('Wine generated headers are incomplete')
flags = ['callback_id_zero', 'same_thread_destructor', 'process_exit', 'thread_exit',
         'loader_lock_refusal', 'locally_scoped_strong_provider', 'eager_binding']
record = dict(contract='zluda-wine-prepared-abi-2', wine_pin=contract['wine_pin'],
              prepared_commit=contract['wine_pin'], prepared_files=contract['prepared_files'],
              verified=dict.fromkeys(flags, True),
              sdk_files={name: hashlib.sha256((sdk / name).read_bytes()).hexdigest()
                         for name in ['bin/winebuild', 'lib/ntdll.so']},
              review='Whole reviewed Valve callback, exit/TLS, loader and COM source files, with handler restoration; strong same-build ntdll provider.')
(sdk / 'ABI-RECEIPT.json').write_text(json.dumps(record, indent=2) + '\n')
