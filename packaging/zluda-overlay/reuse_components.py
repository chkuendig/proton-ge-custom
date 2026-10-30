#!/usr/bin/env python3
"""Explicit manual iteration only: admit unchanged compiled component inputs."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess

from inputs import git, sha256

PREFIX = 'packaging/zluda-overlay/'
ALLOWED = {'proton', 'proton_zluda.py', 'README.md', '.github/workflows/zluda-overlay.yml'} | {
    PREFIX + name for name in ['README.md', 'wine_cpu.py', 'lifecycle_cpu.py', 'launcher_cpu.py',
                              'pinned_fixture_runner.py', 'reuse_components.py']}


def admission_ast(data, functions):
    tree = ast.parse(data)
    tree.body = [node for node in tree.body if not (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions or
        isinstance(node, ast.Import) and all(alias.name == 'reuse_components' for alias in node.names))]
    return ast.dump(tree, include_attributes=False)


def validate_changes(source, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('reuse requires a full source commit')
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True)
    for name in git(source, 'diff', '--name-only', commit, 'HEAD').splitlines():
        if name in ALLOWED or name.startswith('tests/'):
            continue
        functions = {PREFIX + 'overlay.py': {'assemble'}, PREFIX + 'cpu_fixtures.py': {'admit'}}.get(name)
        if functions:
            before = git(source, 'show', commit + ':' + name)
            after = git(source, 'show', 'HEAD:' + name)
            if admission_ast(before, functions) == admission_ast(after, functions):
                continue
        raise ValueError('component reuse changed a build input: ' + name)
    return commit


def expected_commit(source):
    commit = os.environ.get('REUSE_COMPONENTS_COMMIT')
    return validate_changes(source, commit) if commit else git(source, 'rev-parse', 'HEAD')


def receipt(source, run, artifacts):
    if run['repository']['full_name'] != 'chkuendig/proton-ge-custom':
        raise ValueError('unexpected component source repository')
    if run['head_branch'] != 'bridge/nvcuda-guard-20261030':
        raise ValueError('unexpected component source branch')
    commit = validate_changes(source, run['head_sha'])
    required = {'overlay-zluda', 'overlay-bridges', 'wine-cpu-fixtures'}
    selected = [item for item in artifacts['artifacts'] if item['name'] in required]
    if len(selected) != 3 or {item['name'] for item in selected} != required:
        raise ValueError('component artifacts incomplete or ambiguous')
    for item in selected:
        if item['expired'] or not re.fullmatch('sha256:[0-9a-f]{64}', item.get('digest', '')):
            raise ValueError('component artifact expired or missing digest')
    return {'format': 'manual-component-reuse-1', 'run_id': run['id'], 'commit': commit,
            'artifacts': [{key: item[key] for key in ['id', 'name', 'digest', 'size_in_bytes']}
                          for item in selected],
            'qualification': 'Iteration only; frozen W1 candidate requires a full rebuild with reuse off'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'run', 'artifacts', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    record = receipt(args.source, json.loads(args.run.read_text()), json.loads(args.artifacts.read_text()))
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        stream.write('REUSE_COMPONENTS_COMMIT=' + record['commit'] + '\n')
        stream.write('COMPONENT_REUSE_RECEIPT=' + str(args.output) + '\n')
