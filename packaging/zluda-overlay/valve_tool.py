#!/usr/bin/env python3
"""Full Valve source, stock-binary and one-line Wine-delta receipts."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from inputs import LOCK, git, sha256
import wine_provider


def admit(root, output):
    if git(root, 'rev-parse', 'HEAD') != LOCK['valve_stock']['proton']:
        raise ValueError('wrong Valve stock source pin')
    links = {}
    for line in git(root, 'ls-tree', '-r', 'HEAD').splitlines():
        meta, name = line.split('\t', 1)
        if meta.startswith('160000 '):
            links[name] = meta.split()[2]
    if links != LOCK['valve_stock']['gitlinks']:
        raise ValueError('Valve stock gitlinks differ from source lock')
    recursive = {}
    def walk(repo, prefix=''):
        for line in git(repo, 'ls-tree', '-r', 'HEAD').splitlines():
            meta, name = line.split('\t', 1)
            if not meta.startswith('160000 '):
                continue
            child = repo / name
            if git(child, 'rev-parse', 'HEAD') != meta.split()[2]:
                raise ValueError('Valve recursive source pin mismatch: ' + prefix + name)
            recursive[prefix + name] = meta.split()[2]
            walk(child, prefix + name + '/')
    walk(root)
    output.write_text(json.dumps({'proton': LOCK['proton_base'], 'recursive_gitlinks': recursive}, indent=2) + '\n')


def stock(root, archive, output):
    inventory = {}
    for path in sorted((root / 'files/lib/wine').rglob('*')):
        if path.is_file() and not path.is_symlink():
            inventory[path.relative_to(root).as_posix()] = {
                'sha256': sha256(path), 'mtime_ns': path.stat().st_mtime_ns,
                'size': path.stat().st_size,
            }
    inf = root / 'files/share/wine/wine.inf'
    if not inf.is_file():
        raise ValueError('Valve stock lacks wine.inf')
    record = {'format': 'valve-stock-build-1', 'proton_pin': LOCK['proton_base'],
              'wine_pin': LOCK['components']['wine'], 'sdk_image': LOCK['sdk_image'],
              'archive_sha256': sha256(archive), 'wine_modules': inventory,
              'wine_inf': {'sha256': sha256(inf), 'mtime_ns': inf.stat().st_mtime_ns},
              'nvcuda_sha256': {arch: inventory['files/lib/wine/' + arch + '/nvcuda.dll']['sha256']
                                for arch in ['x86_64-windows', 'i386-windows']}}
    output.write_text(json.dumps(record, indent=2) + '\n')
    return record


def delta(overlay, stock_archive, output):
    name = 'files/lib/wine/x86_64-unix/ntdll.so'
    path = overlay / name
    with tarfile.open(stock_archive) as archive:
        members = [m for m in archive if m.name.removeprefix('./') == name]
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('Valve stock callback provider missing/ambiguous')
        before = archive.extractfile(members[0]).read()
    with tempfile.TemporaryDirectory() as tmp:
        original = Path(tmp) / 'ntdll.so'
        original.write_bytes(before)
        old, new = wine_provider.elf_details(original), wine_provider.elf_details(path)
        if old['needed'] != new['needed']:
            raise ValueError('Wine patch changed provider dependency closure')
        versions = lambda p: sorted(set(subprocess.check_output(['readelf', '--version-info', str(p)], text=True).split('Name: ')[1:]))
        # Save full version receipts; SDK/loader checks independently validate resolution.
        traces = {label: subprocess.check_output(['readelf', '--version-info', str(p)], text=True)
                  for label, p in [('stock', original), ('candidate', path)]}
    import re
    needs = lambda text: set(re.findall(r'Name: (\S+)', text))
    if not needs(traces['candidate']) <= needs(traces['stock']):
        raise ValueError('Wine patch introduced a new symbol-version requirement')
    record = {'format': 'valve-wine-scope-1', 'mode': 'valve-single-ntdll',
              'changed_sources': ['dlls/ntdll/unix/signal_x86_64.c'],
              'replaced': {name: sha256(path)},
              'stock_provider_sha256': hashlib.sha256(before).hexdigest(),
              'needed': new['needed'], 'versions': traces}
    output.write_text(json.dumps(record, indent=2) + '\n')


def sources(root, prepared_wine, output):
    # Keep corresponding full source for Valve and every recursive component,
    # plus the exact generated/prepared Wine tree used for the SDK.
    with output.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w|') as archive:
            def include(info):
                if '.git' in Path(info.name).parts or 'autom4te.cache' in Path(info.name).parts:
                    return None
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                return info
            archive.add(root, arcname='valve-proton', filter=include)
            archive.add(prepared_wine, arcname='prepared-wine', filter=include)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['admit', 'stock', 'delta', 'sources', 'normalize'])
    parser.add_argument('root', type=Path)
    parser.add_argument('--epoch', type=int)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--stock', type=Path)
    parser.add_argument('--prepared-wine', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'normalize':
        count = 0
        for path in args.root.rglob('*'):
            if not path.is_symlink():
                os.utime(path, (args.epoch, args.epoch), follow_symlinks=False)
                count += 1
        args.output.write_text(json.dumps({'epoch': args.epoch, 'entries': count}) + '\n')
    elif args.command == 'admit':
        admit(args.root, args.output)
    elif args.command == 'stock':
        stock(args.root, args.archive, args.output)
    elif args.command == 'delta':
        delta(args.root, args.stock, args.output)
    else:
        sources(args.root, args.prepared_wine, args.output)
