#!/usr/bin/env python3
"""Hosted release identity, timestamp and cross-tool builtin inventory audit."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request

from inputs import LOCK, PROFILE


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
    return None if record is None else {k: record[k] for k in ['kind', 'sha256', 'target', 'signature'] if k in record}


def valve_audit(candidate_path, stock_path, control_path, output, a1_path=None):
    if stock_path is None or control_path is None:
        raise ValueError('Valve audit requires independently built stock and published GE control')
    if digest(control_path) != LOCK['comparison_control']['archive_sha256']:
        raise ValueError('published GE control checksum mismatch')
    archives = {'candidate': candidate_path, 'Valve': stock_path, 'GE': control_path}
    if a1_path is not None: archives['A1']=a1_path
    trees = {name: inventory(path, name, flat=name == 'Valve') for name,path in archives.items()}
    candidate,stock,control = (trees[name] for name in ('candidate','Valve','GE'))
    wineinf = 'files/share/wine/wine.inf'
    for field in ('sha256','mode','mtime'):
        if candidate[wineinf][field] != stock[wineinf][field]:
            raise ValueError('wine.inf differs from built Valve stock: '+field)
    report = {'status':'running','archives':{name:{'sha256':digest(path),'bytes':path.stat().st_size} for name,path in archives.items()},
              'wine_inf':{name:tree[wineinf] for name,tree in trees.items()},'inventories':{},'comparisons':{}}
    identity_fields = ('kind','sha256','target','signature','mode','mtime')
    content = lambda record: {k:record[k] for k in identity_fields if k in record} if record else None
    for name,tree in trees.items():
        report['inventories'][name] = {p:record for p,record in tree.items() if builtin(p)}
        if not all(any('/'+arch+'/' in p for p in report['inventories'][name]) for arch in ('i386-windows','x86_64-windows')):
            raise ValueError('incomplete builtin architecture inventory: '+name)
    current=report['inventories']['candidate']
    for name in ['Valve','GE']+(['A1'] if a1_path is not None else []):
        other=report['inventories'][name]
        changes={p:{'candidate':current.get(p),name:other.get(p)} for p in sorted(set(current)|set(other)) if content(current.get(p)) != content(other.get(p))}
        report['comparisons'][name]={'differing_paths':len(changes),'changes':changes}
        lines=['path\tcandidate\t'+name]
        for path,pair in changes.items():lines.append(path+'\t'+json.dumps(pair['candidate'],sort_keys=True)+'\t'+json.dumps(pair[name],sort_keys=True))
        (output/('BUILTIN-DIFF-candidate-'+name+'.tsv')).write_text('\n'.join(lines)+'\n')
    permitted={'files/lib/wine/'+arch+'/nvcuda.dll' for arch in ('i386-windows','x86_64-windows')}
    permitted.add('files/lib/wine/x86_64-unix/ntdll.so')
    for path in report['comparisons']['Valve']['changes']:
        if not path.startswith('files/lib/wine-cuda/') and path not in permitted:
            raise ValueError('unexpected Valve builtin delta: '+path)
    if any('/nvidia-libs/' in path for path in candidate):
        raise ValueError('GE optional NVIDIA stack was imported')
    report['identical_native_runtime']={}
    for path in ['files/lib64/libcuda.so.1', *['files/lib64/'+name for name in LOCK['rocm_runtime']]]:
        if candidate[path]['sha256'] != control[path]['sha256']:
            raise ValueError('native backend/ROCm differs from published GE: '+path)
        report['identical_native_runtime'][path]=candidate[path]['sha256']
    if candidate['files/lib64/libcuda.so.1']['sha256'] != LOCK['comparison_control']['native_sha256']:
        raise ValueError('native control hash differs from lock')
    notice=lambda path: ('licenses' in [part.lower() for part in PurePosixPath(path).parts] or path.endswith('.license') or
        re.match(r'^(licen[sc]e|copying|copyright|notice|patents)([._-]|$)',PurePosixPath(path).name,re.I))
    report['notices']={name:{p:r for p,r in tree.items() if notice(p)} for name,tree in trees.items()}
    report['sources']={name:{p:r for p,r in tree.items() if p.startswith('sources/')} for name,tree in trees.items()}
    for path,record in report['notices']['Valve'].items():
        if content(candidate.get(path)) != content(record):
            raise ValueError('Valve stock notice lost/changed: '+path)
    for path in ['LICENSE','LICENSE.OFL','PATENTS.AV1','sources/valve-proton-sources.tar.gz','sources/zluda-wine.tar.gz',
                 'sources/dxvk-nvapi-prepared.tar.gz','sources/numactl-2.0.18.tar.gz','sources/winetricks/winetricks',
                 'ZLUDA-WINE-NOTICE.md','licenses/zluda-wine/LICENSE-MIT','licenses/zluda-wine/LICENSE-APACHE',
                 'licenses/zluda-wine/LICENSES/PROVENANCE.md']:
        if candidate.get(path,{}).get('kind') != 'file' or not candidate[path]['size']:
            raise ValueError('required source/notice missing: '+path)
    report['status']='passed'
    (output/'RELEASE-AUDIT.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: all Valve builtins, timestamps, notices/sources and eight exact GE native libraries')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--control', type=Path)
    parser.add_argument('--stock', type=Path)
    parser.add_argument('--a1',type=Path)
    for name in ['candidate', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if PROFILE['base'] == 'valve':
        return valve_audit(args.candidate,args.stock,args.control,args.output,args.a1)
    lock = LOCK
    ge = next(x for x in lock['downloads'] if x['id'] == 'ge')
    ge_path = args.output / 'GE.tar.gz'
    urllib.request.urlretrieve(ge['url'], ge_path)
    assert digest(ge_path) == ge['sha256'], 'stock GE archive digest'
    archives = {'B': args.candidate, 'GE': ge_path}
    if args.control:
        archives['published'] = args.control
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
    for name in (n for n in archives if n != 'B'):
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
