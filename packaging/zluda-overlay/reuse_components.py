#!/usr/bin/env python3
"""Explicit manual iteration only: admit unchanged compiled component inputs."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from inputs import HERE, git, sha256

PREFIX = 'packaging/zluda-overlay/'
ALLOWED = {'proton', 'proton_zluda.py', 'default_pfx.py', 'README.md', '.github/workflows/zluda-overlay.yml'} | {
    PREFIX + name for name in ['README.md', 'wine_cpu.py', 'lifecycle_cpu.py', 'launcher_cpu.py',
                              'pinned_fixture_runner.py', 'reuse_components.py']}
ALLOWED.add(PREFIX + 'release_audit.py')  # Artifact inspection cannot change compiled inputs.


def admission_ast(data, functions):
    tree = ast.parse(data)
    tree.body = [node for node in tree.body if not (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions or
        isinstance(node, ast.Import) and all(alias.name == 'reuse_components' for alias in node.names))]
    return ast.dump(tree, include_attributes=False)


def validate_changes(source, commit, part='all'):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('reuse requires a full source commit')
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True)
    for name in git(source, 'diff', '--name-only', commit, 'HEAD').splitlines():
        if name in ALLOWED or name.startswith('tests/'):
            continue
        if part == 'bridges' and name in {'zluda', PREFIX + 'inputs.lock.json'}:
            if name == 'zluda':
                continue  # Not used by the separately pinned bridge component.
            before, after = [json.loads(git(source, 'show', ref + ':' + name))
                             for ref in [commit, 'HEAD']]
            for lock in [before, after]:
                lock.pop('comparison_control', None)  # Test control, never compiled.
                lock['components'].pop('zluda')  # Native backend is rebuilt separately.
            if before == after:
                continue
        functions = {PREFIX + 'overlay.py': {'assemble'}, PREFIX + 'cpu_fixtures.py': {'admit'},
                     PREFIX + 'cuda_package.py': {'materialize_template', 'ge_cuda_path', 'audit'}}.get(name)
        if functions:
            before = git(source, 'show', commit + ':' + name)
            after = git(source, 'show', 'HEAD:' + name)
            if admission_ast(before, functions) == admission_ast(after, functions):
                continue
        raise ValueError('component reuse changed a build input: ' + name)
    return commit


def expected_commit(source, part='bridges'):
    commit = os.environ.get('REUSE_COMPONENTS_COMMIT')
    if commit:
        return validate_changes(source, commit)
    commit = os.environ.get('REUSE_BRIDGES_COMMIT') if part == 'bridges' else None
    return validate_changes(source, commit, 'bridges') if commit else git(source, 'rev-parse', 'HEAD')


def expected_lock_sha256(source, part='bridges'):
    if part == 'bridges' and os.environ.get('REUSE_BRIDGES_COMMIT'):
        commit = expected_commit(source, part)
        data = subprocess.check_output(['git', '-C', str(source), 'show',
                                        commit + ':' + PREFIX + 'inputs.lock.json'])
        return hashlib.sha256(data).hexdigest()
    return sha256(HERE / 'inputs.lock.json')


def receipt(source, run, artifacts, part='all'):
    if run['repository']['full_name'] != 'chkuendig/proton-ge-custom':
        raise ValueError('unexpected component source repository')
    if run['head_branch'] != 'valve/11.0-zluda-20261104':
        raise ValueError('unexpected component source branch')
    commit = validate_changes(source, run['head_sha'], part)
    required = {'overlay-zluda', 'overlay-bridges', 'wine-cpu-fixtures', 'w1-fixtures'}
    if part == 'bridges':
        required.remove('overlay-zluda')
    selected = [item for item in artifacts['artifacts'] if item['name'] in required]
    if len(selected) != len(required) or {item['name'] for item in selected} != required:
        raise ValueError('component artifacts incomplete or ambiguous')
    for item in selected:
        if item['expired'] or not re.fullmatch('sha256:[0-9a-f]{64}', item.get('digest', '')):
            raise ValueError('component artifact expired or missing digest')
    return {'format': 'manual-component-reuse-1', 'run_id': run['id'], 'commit': commit, 'part': part,
            'artifacts': [{key: item[key] for key in ['id', 'name', 'digest', 'size_in_bytes']}
                          for item in selected],
            'qualification': 'Explicit source-admitted reuse; original component build commit retained'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'run', 'artifacts', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--part', choices=['all', 'bridges'], default='all')
    args = parser.parse_args()
    record = receipt(args.source, json.loads(args.run.read_text()), json.loads(args.artifacts.read_text()), args.part)
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        variable = 'REUSE_BRIDGES_COMMIT' if args.part == 'bridges' else 'REUSE_COMPONENTS_COMMIT'
        stream.write(variable + '=' + record['commit'] + '\n')
        stream.write('COMPONENT_REUSE_RECEIPT=' + str(args.output) + '\n')
