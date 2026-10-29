#!/usr/bin/env python3
"""Regenerate the projected native fixture from the pinned source Git repository."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib


def generate(repo):
    lock_path = Path(__file__).resolve().parents[2] / 'packaging/zluda-overlay/inputs.lock.json'
    pin = json.loads(lock_path.read_text())['components']['zluda']
    lock = subprocess.check_output(['git', '-C', str(repo), 'show', pin + ':Cargo.lock'])
    packages = tomllib.loads(lock.decode())['package']
    names = ['zluda', 'ptx', 'llvm_zluda', 'highs', 'highs-sys', 'cuda_types', 'dark_api',
             'zluda_cache', 'blake3', 'vergen-gix', 'lz4-sys', 'zstd-safe', 'zstd-sys',
             'libsqlite3-sys', 'hip_runtime-sys', 'hipblaslt-sys', 'hipfft-sys', 'miopen-sys',
             'rocblas-sys', 'rocsparse-sys', 'rocm_smi-sys']
    chosen = {name: next(p for p in packages if p['name'] == name) for name in names}
    ids = {name: p.get('source', 'path+file:///fixture/zluda/' + name) + '#' + name + '@' + p['version']
           for name, p in chosen.items()}
    records = [{'name': p['name'], 'version': p['version'], 'id': ids[name]} for name, p in chosen.items()]
    nodes = []
    for name, package in chosen.items():
        deps = []
        for dependency in package.get('dependencies', []):
            dep = dependency.split()[0]
            if dep in chosen:
                kind = 'build' if name == 'zluda' and dep == 'vergen-gix' else None
                deps.append({'name': dep.replace('-', '_'), 'pkg': ids[dep],
                             'dep_kinds': [{'kind': kind, 'target': None}]})
        nodes.append({'id': ids[name], 'deps': deps})
    metadata = {'packages': records,
                'workspace_members': [ids[name] for name, p in chosen.items() if 'source' not in p],
                'resolve': {'nodes': nodes}}
    features = {'highs-sys': 'build,default,highs_release',
                'zstd-sys': 'default,legacy,std,zdict_builder',
                'libsqlite3-sys': 'bundled,bundled_bindings,cc'}
    tree = '\n'.join(f"{name} v{p['version']}" +
                     (' (/fixture/zluda/' + name + ')' if 'source' not in p else '') +
                     '|' + features.get(name, '') for name, p in chosen.items()) + '\n'
    result = {'origin': {'pin': pin, 'Cargo.lock_sha256': hashlib.sha256(lock).hexdigest(),
                         'method': 'Projected Linux normal/build graph: package identities and dependency edges '
                                   'from pinned Cargo.lock; curated active features from manifests. '
                                   'Not captured cargo output; unrelated nodes omitted.'},
              'metadata': metadata, 'tree': tree}
    Path(__file__).with_name('native-graph.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('repository', type=Path)
    generate(parser.parse_args().repository)
