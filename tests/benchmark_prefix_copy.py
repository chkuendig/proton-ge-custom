"""Compare B3 and current prefix file copies; run only when host CI is idle.

Usage: python3 tests/benchmark_prefix_copy.py EXTRACTED_FILES_DIR WORK_DIR
The work directory must be on the filesystem under test and initially empty.
"""
import ast
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def launcher(source):
    names = {'try_copyfile', 'atomic_copy', 'copyfile_fds', 'copyfile_reflink'}
    ns = dict(os=os, shutil=shutil, stat=stat, uuid=uuid, errno=errno,
              copy_file_range=os.copy_file_range, log=lambda *args: None,
              file_exists=lambda path, follow_symlinks=True: os.path.exists(path) if follow_symlinks else os.path.lexists(path))
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'proton', 'exec'), ns)
    ns['copyfile'] = ns['copyfile_reflink']
    return ns['try_copyfile']


def main():
    source, work = map(Path, sys.argv[1:])
    if any(work.iterdir()):
        raise SystemExit('work directory must be empty')
    files = sorted(p for arch in ('i386-windows', 'x86_64-windows')
                   for p in (source / 'lib/wine' / arch).glob('*.dll') if p.is_file())
    if not files or not all(any(arch in str(p) for p in files) for arch in ('i386-windows', 'x86_64-windows')):
        raise SystemExit('both Wine Windows architecture sets are required')
    old = subprocess.check_output(['git', '-C', str(ROOT), 'show', '0bda91ad:proton'], text=True)
    new = (ROOT / 'proton').read_text()
    results = []
    for label, text in (('b3', old), ('atomic', new)):
        copy = launcher(text)
        target = work / label
        for arch in ('i386-windows', 'x86_64-windows'):
            (target / arch).mkdir(parents=True)
        start = time.monotonic()
        for path in files:
            copy(str(path), str(target / path.parent.name / path.name))
        elapsed = time.monotonic() - start
        count = sum(p.stat().st_size for p in files)
        output = [target / p.parent.name / p.name for p in files]
        if sum(p.stat().st_size for p in output) != count:
            raise RuntimeError('copied byte count differs')
        results.append(dict(implementation=label, files=len(files), bytes=count,
                            allocated_bytes=sum(p.stat().st_blocks * 512 for p in output),
                            seconds=round(elapsed, 3)))
    print(json.dumps(dict(source=str(source), results=results), indent=2))


if __name__ == '__main__':
    main()
