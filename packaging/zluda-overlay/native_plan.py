#!/usr/bin/env python3
"""Plan early Cargo builds from the resolved Linux zluda graph."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

TARGET = 'x86_64-unknown-linux-gnu'
WORKSPACE_ROOTS = ['highs-sys', 'cuda_types', 'dark_api', 'zluda_cache']
OPTIONAL_DEPENDENCIES = ['highs', 'blake3', 'vergen-gix', 'lz4-sys', 'zstd-sys', 'zstd']
REQUIRED = {'highs-sys', 'cuda_types', 'llvm_zluda', 'zluda'}


def tree_packages(text):
    packages = {}
    for line in text.splitlines():
        line = re.sub(r'\s+\(\*\)$', '', line.strip())
        if not line or re.fullmatch(r'\[[^]]+\]', line):
            continue
        match = re.fullmatch(r'([\w-]+) v(\S+)(?: \([^|]*\))?\|([^|]*)', line)
        if not match:
            raise ValueError('unexpected Cargo tree record: ' + line)
        key = match[1], match[2]
        packages.setdefault(key, set()).update(filter(None, match[3].split(',')))
    return packages


def plan(metadata, tree):
    active = tree_packages(tree)
    packages = {p['id']: p for p in metadata['packages']}
    members = set(metadata['workspace_members'])
    nodes = {node['id']: node for node in metadata['resolve']['nodes']}
    active_ids = {pid for pid, p in packages.items() if (p['name'], p['version']) in active}
    names = {packages[pid]['name'] for pid in active_ids}
    if REQUIRED - names:
        raise ValueError('required native graph packages missing: ' + ', '.join(sorted(REQUIRED - names)))

    def closure(pid):
        seen, pending = set(), [pid]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            for dep in nodes[current]['deps']:
                if any(kind['kind'] in (None, 'build') for kind in dep['dep_kinds']):
                    pending.append(dep['pkg'])
        return seen

    selected, covered, skipped = [], set(), {}
    for name in WORKSPACE_ROOTS:
        candidates = [pid for pid in active_ids & members if packages[pid]['name'] == name]
        if len(candidates) != 1:
            if name in REQUIRED:
                raise ValueError('required native workspace root is missing or ambiguous: ' + name)
            skipped[name] = 'not a unique resolved workspace root'
            continue
        pid = candidates[0]
        dependencies = closure(pid)
        if any(packages[dep]['name'] == 'llvm_zluda' for dep in dependencies):
            if name in REQUIRED:
                raise ValueError('required native root would start LLVM: ' + name)
            skipped[name] = 'would start LLVM'
            continue
        selected.append(pid)
        covered.update(dependencies)
    args = ['--no-default-features']
    features = []
    for pid in selected:
        package = packages[pid]
        args.extend(['-p', pid])
        features.extend(package['name'] + '/' + feature for feature in
                        sorted(active[(package['name'], package['version'])]))
    if features:
        args.extend(['--features', ','.join(features)])
    for name in OPTIONAL_DEPENDENCIES:
        matching = [pid for pid in active_ids if packages[pid]['name'] == name]
        skipped[name] = ('absent from final target graph' if not matching else
                         'built transitively by an early workspace root' if any(pid in covered for pid in matching)
                         else 'deferred to final build; not an early workspace root')
    record = {'target': TARGET, 'final_package': 'zluda', 'roots': selected,
              'features': features, 'optional': skipped, 'args': args}
    validate_specs(record, args)
    return record


def validate_specs(record, args):
    """Do not turn lockfile/registry membership into an unresolvable -p spec."""
    for index, arg in enumerate(args):
        if arg == '-p' and (index + 1 == len(args) or args[index + 1] not in record['roots']):
            raise ValueError('unresolvable early package spec: ' + (args[index + 1] if index + 1 < len(args) else '<missing>'))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output(['rustc', '-vV'], text=True)
    if ('host: ' + TARGET) not in version.splitlines() or os.environ.get('CARGO_BUILD_TARGET'):
        raise ValueError('native build requires the pinned Linux host/default target')
    manifest = str(Path('zluda/Cargo.toml').resolve())
    commands = [(['cargo', 'metadata', '--locked', '--offline', '--format-version', '1',
                 '--filter-platform', TARGET, '--manifest-path', manifest], 'metadata.json'),
                (['cargo', 'tree', '--locked', '--offline', '--target', TARGET,
                  '--manifest-path', manifest, '-p', 'zluda', '-e', 'normal,build',
                  '--prefix', 'none', '--format', '{p}|{f}'], 'zluda-tree.txt')]
    for command, filename in commands:
        with (args.output / filename).open('w') as out, (args.output / (filename + '.stderr')).open('w') as err:
            subprocess.run(command, stdout=out, stderr=err, check=True)
    record = plan(json.loads((args.output / 'metadata.json').read_text()),
                  (args.output / 'zluda-tree.txt').read_text())
    (args.output / 'plan.json').write_text(json.dumps(record, indent=2) + '\n')
    (args.output / 'early-args.nul').write_bytes(('\0'.join(record['args']) + '\0').encode())


if __name__ == '__main__':
    main()
