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

from inputs import git, sha256

PREFIX = 'packaging/zluda-overlay/'
ALLOWED = {'proton', 'proton_zluda.py', 'default_pfx.py', 'README.md', '.github/workflows/zluda-overlay.yml'} | {
    PREFIX + name for name in ['README.md', 'wine_cpu.py', 'lifecycle_cpu.py', 'launcher_cpu.py',
                              'pinned_fixture_runner.py', 'reuse_components.py', 'build-nvapi.sh',
                              'nvapi_package.py', 'nvapi_cpu.py', 'stage_cpu_evidence.py', 'nvapi_provider.py',
                              'nvapi-provider-probe.cpp', 'nvapi-provider-backend.c']}


def original_lock(source, commit):
    return git(source, 'show', commit + ':' + PREFIX + 'inputs.lock.json')


def validate_lock(before, after):
    before, after = json.loads(before), json.loads(after)
    # The reused bridge/backend bytes are unchanged. NVAPI is rebuilt in a
    # separate, mandatory job and replaces the old NVAPI payload and sources.
    for lock in [before, after]:
        lock['component_patches'].pop('dxvk-nvapi')
    if before != after:
        raise ValueError('component reuse changed a non-NVAPI lock input')


def expected_lock_sha256(source):
    commit = os.environ.get('REUSE_COMPONENTS_COMMIT')
    if commit:
        validate_changes(source, commit)
        # git() strips trailing whitespace; JSON is stored with one newline.
        return hashlib.sha256((original_lock(source, commit) + '\n').encode()).hexdigest()
    return sha256(Path(__file__).with_name('inputs.lock.json'))


def admission_ast(data, functions):
    tree = ast.parse(data)
    tree.body = [node for node in tree.body if not (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions or
        isinstance(node, ast.Import) and all(alias.name in (
            {'reuse_components', 'nvapi_package'} if 'assemble' in functions else {'reuse_components'})
            for alias in node.names))]
    return ast.dump(tree, include_attributes=False)


def validate_changes(source, commit):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('reuse requires a full source commit')
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True)
    for name in git(source, 'diff', '--name-only', commit, 'HEAD').splitlines():
        if name in ALLOWED or name.startswith('tests/'):
            continue
        if name.startswith('patches/dxvk-nvapi/'):
            continue
        if name == PREFIX + 'inputs.lock.json':
            validate_lock(original_lock(source, commit), original_lock(source, 'HEAD'))
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


def expected_commit(source):
    commit = os.environ.get('REUSE_COMPONENTS_COMMIT')
    return validate_changes(source, commit) if commit else git(source, 'rev-parse', 'HEAD')


def receipt(source, run, artifacts):
    if run['repository']['full_name'] != 'chkuendig/proton-ge-custom':
        raise ValueError('unexpected component source repository')
    if run['head_branch'] not in ('bridge/w1-candidate-20261031', 'bridge/dxvk-nvapi-provider-20261031'):
        raise ValueError('unexpected component source branch')
    if run['status'] != 'completed' or run['conclusion'] != 'success':
        raise ValueError('component source run is not successful')
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
            'qualification': 'Explicit provider integration reuse; frozen W1 candidate unchanged, NVAPI always rebuilt'}


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
