#!/usr/bin/env python3
"""Hosted release identity, timestamp and cross-tool builtin inventory audit."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def inventory(path, label, flat=False):
    entries = {}
    with tarfile.open(path, 'r|gz') as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if not parts or '..' in parts or member.name.startswith('/'):
                raise ValueError('unsafe archive member')
            name = '/'.join(parts if flat else parts[1:])
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
    return None if record is None else {k: record[k] for k in ['kind', 'sha256', 'target', 'signature', 'mtime'] if k in record}


SOURCE_AND_NOTICE_REQUIRED = [
    'sources/valve-proton-sources.tar.gz', 'sources/zluda-wine.tar.gz',
    'sources/dxvk-nvapi-prepared.tar.gz', 'sources/numactl-2.0.18.tar.gz',
    'sources/winetricks/winetricks', 'ZLUDA-WINE-NOTICE.md',
    'licenses/zluda-wine/LICENSE-MIT', 'licenses/zluda-wine/LICENSE-APACHE',
    'licenses/zluda-wine/LICENSES/PROVENANCE.md',
]


def audit_sources_and_notices(trees):
    def notice(path):
        parts = PurePosixPath(path).parts
        return ('licenses' in [p.lower() for p in parts] or path.endswith('.license') or
                bool(re.match(r'^(licen[sc]e|copying|copyright|notice|patents)([._-]|$)', parts[-1], re.I)) or
                path == 'ZLUDA-WINE-NOTICE.md')

    notices = {label: {p: r for p, r in tree.items() if notice(p)} for label, tree in trees.items()}
    sources = {label: {p: r for p, r in tree.items() if p.startswith('sources/')} for label, tree in trees.items()}
    candidate, stock = trees['candidate'], trees['Valve']
    for path in ['LICENSE', 'LICENSE.OFL', 'PATENTS.AV1']:
        if stock.get(path, {}).get('kind') != 'file' or not stock[path]['size']:
            raise ValueError('incomplete Valve stock notice inventory: ' + path)
    content = lambda r: None if r is None else {k: r[k] for k in ['kind', 'sha256', 'target'] if k in r}
    for path, original in notices['Valve'].items():
        if content(candidate.get(path)) != content(original):
            raise ValueError('Valve stock notice lost or changed: ' + path)
    for path in SOURCE_AND_NOTICE_REQUIRED:
        if candidate.get(path, {}).get('kind') != 'file' or not candidate[path]['size']:
            raise ValueError('required source or notice missing: ' + path)
    comparisons = {}
    for label, inventories in [('notices', notices), ('sources', sources)]:
        comparisons[label] = {other: {
            p: {'candidate': inventories['candidate'].get(p), other: inventories[other].get(p)}
            for p in sorted(set(inventories['candidate']) | set(inventories[other]))
            if content(inventories['candidate'].get(p)) != content(inventories[other].get(p))
        } for other in ['A1', 'B5', 'Valve']}
    return {'scope': 'all retained notices and source files; Valve notices preserved; required modified-source bundles present',
            'notices': notices, 'sources': sources, 'comparisons': comparisons,
            'required_sources_and_notices': SOURCE_AND_NOTICE_REQUIRED}


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'candidate', 'a1', 'stock', 'control', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    lock = json.loads((args.source / 'packaging/zluda-overlay/inputs.lock.json').read_text())
    archives = {'candidate': args.candidate, 'A1': args.a1, 'B5': args.control, 'Valve': args.stock}
    if digest(args.control) != lock['comparison_control']['archive_sha256']:
        raise ValueError('comparison control is not the published B5 product archive')
    trees = {name: inventory(path, name, flat=name == 'Valve') for name, path in archives.items()}
    wineinf = 'files/share/wine/wine.inf'
    candidate, stock = trees['candidate'], trees['Valve']
    for field in ['sha256', 'mtime']:
        if candidate[wineinf][field] != stock[wineinf][field]:
            raise ValueError('wine.inf ' + field + ' differs from built Valve stock')
    report = {'status': 'passed', 'scope': 'every canonical Wine architecture-root file and per-launch CUDA module; complete source/license inventories retained',
              'archives': {n: {'sha256': digest(p), 'bytes': p.stat().st_size} for n, p in archives.items()},
              'wine_inf': {n: t[wineinf] for n, t in trees.items()}, 'comparisons': {}, 'inventories': {}}
    report['sources_and_notices'] = audit_sources_and_notices(trees)
    for name, tree in trees.items():
        report['inventories'][name] = {p: r for p, r in tree.items() if builtin(p)}
        if not all(any('/' + arch + '/' in p for p in report['inventories'][name])
                   for arch in ['i386-windows', 'x86_64-windows']):
            raise ValueError('incomplete builtin architecture inventory: ' + name)
    current = report['inventories']['candidate']
    for name in ['A1', 'B5', 'Valve']:
        other = report['inventories'][name]
        changes = {p: {'candidate': current.get(p), name: other.get(p)}
                   for p in sorted(set(current) | set(other)) if identity(current.get(p)) != identity(other.get(p))}
        report['comparisons'][name] = {'differing_paths': len(changes), 'changes': changes}
        lines = ['path\tcandidate_kind\tcandidate_sha256_or_target\t' + name + '_kind\t' + name + '_sha256_or_target']
        for path, pair in changes.items():
            fields = [path]
            for key in ['candidate', name]:
                record = pair[key] or {}
                fields += [record.get('kind', 'absent'), record.get('sha256', record.get('target', '-'))]
            lines.append('\t'.join(fields))
        (args.output / ('BUILTIN-DIFF-candidate-' + name + '.tsv')).write_text('\n'.join(lines) + '\n')
    permitted = {'files/lib/wine/' + arch + '/nvcuda.dll' for arch in ['x86_64-windows', 'i386-windows']}
    permitted.add('files/lib/wine/x86_64-unix/ntdll.so')
    for path in report['comparisons']['Valve']['changes']:
        if not path.startswith('files/lib/wine-cuda/') and path not in permitted:
            raise ValueError('unexpected Valve Wine builtin delta: ' + path)
    if any('/nvidia-libs/' in path for path in candidate):
        raise ValueError('GE optional NVIDIA stack was imported')
    same = ['files/lib64/libcuda.so.1'] + ['files/lib64/' + real for real in lock['rocm_runtime']]
    report['B5_identical_native_runtime'] = {}
    for path in same:
        if candidate[path]['sha256'] != trees['B5'][path]['sha256']:
            raise ValueError('native backend/ROCm must remain identical to B5: ' + path)
        report['B5_identical_native_runtime'][path] = candidate[path]['sha256']
    (args.output / 'RELEASE-AUDIT.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Release audit PASS; all builtin inventories, stock wine.inf and B5 native runtime checked')


if __name__ == '__main__':
    main()
