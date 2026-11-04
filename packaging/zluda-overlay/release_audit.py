#!/usr/bin/env python3
"""Hosted release identity, timestamp and cross-tool builtin inventory audit."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request

from inputs import LOCK


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def inventory(path, label):
    entries = {}
    with tarfile.open(path, 'r|gz') as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or '..' in parts or member.name.startswith('/'):
                raise ValueError('unsafe archive member')
            name = '/'.join(parts[1:])
            if not name:
                continue
            record = {'mtime': member.mtime, 'mode': member.mode, 'size': member.size}
            if member.isfile():
                stream = archive.extractfile(member)
                h = hashlib.sha256()
                first = b''
                knight = b''
                for data in iter(lambda: stream.read(1024 * 1024), b''):
                    if not first:
                        first = data[:128]
                    h.update(data)
                    if name == 'protonfixes/gamefixes-steam/208650.py':
                        knight += data
                record.update(kind='file', sha256=h.hexdigest())
                if b'Wine builtin DLL' in first:
                    record['signature'] = 'Wine builtin DLL'
                elif b'Wine placeholder DLL' in first:
                    record['signature'] = 'Wine placeholder DLL'
                if name == 'protonfixes/gamefixes-steam/208650.py':
                    if label == 'B':
                        assert b'ZLUDA_D3D11_CPU_COPY' not in knight, 'Knight relay setter survived'
            elif member.issym() or member.islnk():
                record.update(kind='symlink' if member.issym() else 'hardlink', target=member.linkname)
            else:
                continue
            entries[name] = record
    return entries


def builtin(name):
    return bool(re.fullmatch(r'files/lib(?:64)?/wine/[^/]+-(?:windows|unix)/[^/]+', name) or
                re.fullmatch(r'files/lib(?:64)?/wine-cuda/[^/]+/[^/]+-(?:windows|unix)/[^/]+', name))


def identity(record):
    return None if record is None else {k: record[k] for k in ['kind', 'sha256', 'target', 'signature'] if k in record}


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ['candidate', 'control', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = LOCK
    ge = next(x for x in lock['downloads'] if x['id'] == 'ge')
    ge_path = args.output / 'GE.tar.gz'
    urllib.request.urlretrieve(ge['url'], ge_path)
    assert digest(ge_path) == ge['sha256'], 'stock GE archive digest'
    archives = {'B': args.candidate, 'published': args.control, 'GE': ge_path}
    trees = {name: inventory(path, name) for name, path in archives.items()}
    wineinf = 'files/share/wine/wine.inf'
    assert trees['B'][wineinf]['sha256'] == trees['GE'][wineinf]['sha256'], 'wine.inf bytes changed'
    assert trees['B'][wineinf]['mtime'] == trees['GE'][wineinf]['mtime'], 'wine.inf mtime differs from GE'
    report = {'status': 'passed', 'scope': 'all canonical Wine architecture-root files (PE, Unix and aliases), plus all per-launch CUDA builtin roots; timestamps excluded from byte/link identity comparisons',
              'archives': {n: {'sha256': digest(p), 'bytes': p.stat().st_size} for n, p in archives.items()},
              'wine_inf': {n: t[wineinf] for n, t in trees.items()},
              'source_protonfixes': lock['components']['protonfixes'], 'comparisons': {}, 'inventories': {}}
    for name, tree in trees.items():
        report['inventories'][name] = {p: r for p, r in tree.items() if builtin(p)}
        assert any('/i386-windows/' in p for p in report['inventories'][name])
        assert any('/x86_64-windows/' in p for p in report['inventories'][name])
    b = report['inventories']['B']
    for name in ['published', 'GE']:
        other = report['inventories'][name]
        changes = {p: {'B': b.get(p), name: other.get(p)} for p in sorted(set(b) | set(other)) if identity(b.get(p)) != identity(other.get(p))}
        report['comparisons'][name] = {'differing_paths': len(changes), 'changes': changes,
                                     'identical_paths': sum(identity(b.get(p)) == identity(other.get(p)) for p in set(b) | set(other))}
        lines = ['path\tB_kind\tB_sha256_or_target\t' + name + '_kind\t' + name + '_sha256_or_target']
        for p, pair in changes.items():
            fields = [p]
            for key in ['B', name]:
                r = pair[key] or {}; fields += [r.get('kind', 'absent'), r.get('sha256', r.get('target', '-'))]
            lines.append('\t'.join(fields))
        (args.output / ('BUILTIN-DIFF-B-' + name + '.tsv')).write_text('\n'.join(lines) + '\n')
    (args.output / 'RELEASE-AUDIT.json').write_text(json.dumps(report, indent=2) + '\n')
    ge_path.unlink()
    print('Release audit PASS; builtin differing paths:', {n: c['differing_paths'] for n, c in report['comparisons'].items()})
    print('wine.inf mtimes:', {n: t[wineinf]['mtime'] for n, t in trees.items()})


if __name__ == '__main__':
    main()
