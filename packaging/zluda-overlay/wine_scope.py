#!/usr/bin/env python3
"""Narrow the one known Unix-only Wine delta, preserving GE's other ntdlls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

SOURCE = 'dlls/ntdll/unix/signal_x86_64.c'
OUTPUT = 'files/lib/wine/x86_64-unix/ntdll.so'
PRESERVED = ['files/lib/wine/' + name for name in
             ['i386-windows/ntdll.dll', 'i386-unix/ntdll.so', 'x86_64-windows/ntdll.dll']]
REPLACED_PE32 = 'files/lib/wine/i386-windows/nvcuda.dll'


def preserved_path(name):
    return '..' not in Path(name).parts and (name in PRESERVED or
           name != REPLACED_PE32 and name.startswith(
               ('files/lib/wine/i386-unix/', 'files/lib/wine/i386-windows/')))


def select(repo, baseline, head):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    paths = git('diff', '--name-only', baseline, head, '--').decode().splitlines()
    record = {'format': 'zluda-wine-scope-1', 'mode': 'both',
              'baseline': git('rev-parse', baseline).decode().strip(),
              'head': git('rev-parse', head).decode().strip(), 'changed_sources': paths}
    if paths == [SOURCE]:
        before, after = [git('show', rev + ':' + SOURCE) for rev in [baseline, head]]
        needle = b'            RIP_sig(ucontext) = (intptr_t)steamclient_addr;\n            return;\n'
        fixed = needle.replace(b'            return;', b'            leave_handler( ucontext );\n            return;')
        # Check the complete prepared-file transformation, not just its name or
        # an x86_64-looking source path. Any other change retains both builds.
        if before.count(needle) == 1 and after == before.replace(needle, fixed):
            record.update(mode='x86_64-unix-ntdll', output=OUTPUT,
                          source_before_sha256=hashlib.sha256(before).hexdigest(),
                          source_after_sha256=hashlib.sha256(after).hexdigest())
    return record


def inventory(archive, names, sizes, scope):
    record = json.loads(scope.read_text())
    preserved, links = {}, {}
    with tarfile.open(archive) as stream, names.open('w') as paths, sizes.open('w') as lengths:
        for member in stream:
            path = member.name.split('/', 1)[-1]
            keep = record['mode'] == 'x86_64-unix-ntdll' and preserved_path(path)
            if keep and member.issym():
                links[path] = member.linkname
            if keep and member.islnk():
                preserved[path] = hashlib.sha256(stream.extractfile(member).read()).hexdigest()
            if not member.isfile():
                continue
            paths.write(path + '\n')
            lengths.write(f'{path}\t{member.size}\n')
            if keep:
                preserved[path] = hashlib.sha256(stream.extractfile(member).read()).hexdigest()
    if record['mode'] == 'x86_64-unix-ntdll':
        if not set(PRESERVED).issubset(preserved):
            raise ValueError('GE archive missing preserved ntdll counterpart')
        record['ge_preserved_sha256'] = preserved
        record['ge_preserved_links'] = links
        record['intentional_i386_replacements'] = [REPLACED_PE32]
    scope.write_text(json.dumps(record, indent=2) + '\n')


def verify(root, record):
    if record['mode'] not in ('both', 'x86_64-unix-ntdll'):
        raise ValueError('unknown Wine scope mode')
    if record['mode'] != 'x86_64-unix-ntdll':
        return
    hashes = record['ge_preserved_sha256']
    if not set(PRESERVED).issubset(hashes) or not all(map(preserved_path, hashes)):
        raise ValueError('incomplete preserved GE ntdll inventory')
    for name, expected in hashes.items():
        path = root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('preserved GE Wine file differs: ' + name)
    for name, expected in record['ge_preserved_links'].items():
        path = root / name
        if not preserved_path(name) or not path.is_symlink() or os.readlink(path) != expected:
            raise ValueError('preserved GE Wine link differs: ' + name)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('repo', type=Path)
    parser.add_argument('baseline')
    parser.add_argument('head')
    parser.add_argument('record', type=Path)
    args = parser.parse_args()
    record = select(args.repo, args.baseline, args.head)
    args.record.write_text(json.dumps(record, indent=2) + '\n')
    print(record['mode'])


if __name__ == '__main__':
    main()
