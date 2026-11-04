#!/usr/bin/env python3
"""Resolve the replaceable bridge component from the single locked input."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.request

from inputs import LOCK, git, sha256


def pin():
    return LOCK['components'][LOCK['wine_bridge']['component']]


def source(proton):
    return Path(proton) / LOCK['wine_bridge']['component']


def relative_package(component):
    return Path(component) / LOCK['wine_bridge']['path']


def package(proton):
    return relative_package(source(proton))


def fixtures(proton):
    return source(proton) / LOCK['wine_bridge']['fixture_path']


def backend_source(proton):
    return Path(proton) / 'bridge-backend'


def extracted_package(root):
    record = json.loads((root / 'external/BRIDGE-BUILD.json').read_text())
    relative = Path(record['bridge_path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('invalid extracted bridge path')
    return root / relative


def admit(proton):
    component, spec = source(proton), LOCK['wine_bridge']
    if git(proton, 'rev-parse', 'HEAD:' + spec['component']) != pin():
        raise ValueError('bridge parent gitlink differs from lock')
    if git(component, 'rev-parse', 'HEAD') != pin():
        raise ValueError('bridge component pin differs from lock')
    if git(component, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=all'):
        raise ValueError('dirty bridge component')
    if git(component, 'rev-parse', 'HEAD:' + spec['path']) != spec['tree']:
        raise ValueError('bridge crate tree differs from lock')
    if sha256(package(proton) / 'Cargo.lock') != spec['cargo_lock_sha256']:
        raise ValueError('bridge crate Cargo.lock differs from lock')


def checkout(proton):
    subprocess.run(['git', '-C', str(proton), 'submodule', 'update', '--init',
                    '--depth=1', '--filter=blob:none', '--', LOCK['wine_bridge']['component']], check=True)
    admit(proton)
    contract = json.loads((source(proton) / 'BACKEND-SOURCE.json').read_text())
    root = backend_source(proton)
    root.mkdir(exist_ok=True)
    repository = contract['repository'].removesuffix('.git').removeprefix('https://github.com/')
    for name, expected in contract['source_inputs'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid backend declaration path')
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or sha256(target) != expected:
            url = 'https://raw.githubusercontent.com/' + repository + '/' + contract['commit'] + '/' + name
            with urllib.request.urlopen(url, timeout=60) as response:
                target.write_bytes(response.read())
        if sha256(target) != expected:
            raise ValueError('backend declaration differs from bridge contract: ' + name)
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as output:
            output.write('ZLUDA_WINE_BACKEND_SOURCE=' + str(root.resolve()) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['checkout', 'source', 'package', 'fixtures', 'pin', 'backend-source'])
    parser.add_argument('proton', type=Path, nargs='?', default=Path('.'))
    args = parser.parse_args()
    if args.command == 'checkout':
        checkout(args.proton.resolve())
    elif args.command == 'pin':
        print(pin())
    elif args.command == 'backend-source':
        print(backend_source(args.proton.resolve()))
    else:
        print(globals()[args.command](args.proton.resolve()))
