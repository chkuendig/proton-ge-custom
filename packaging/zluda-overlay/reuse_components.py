#!/usr/bin/env python3
"""Explicit manual iteration only: admit unchanged compiled component inputs."""
import argparse
import ast
import configparser
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
ALLOWED.update(PREFIX + name for name in ['ci-steam-video.sh', 'check-loader.sh', 'w1_cpu.py'])


def admission_ast(data, functions):
    tree = ast.parse(data)
    tree.body = [node for node in tree.body if not (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions or
        isinstance(node, ast.Import) and all(alias.name == 'reuse_components' for alias in node.names))]
    return ast.dump(tree, include_attributes=False)


def native_recipe(data):
    """Keep native/common commands exact when only the bridge arm changes."""
    start = data.index('if [[ $part == bridges || $part == all ]]; then')
    end = data.index('\nDESTDIR=$DESTDIR python3', start)
    return data[:start] + data[end:]


def native_modules(data):
    """The independent bridge's retrieval URL is not a native compiler input."""
    modules = configparser.ConfigParser(interpolation=None)
    modules.read_string(data)
    section = 'submodule "wine-bridge"'
    bridge = dict(modules[section])
    if set(bridge) != {'path', 'url'} or bridge['path'] != 'wine-bridge':
        raise ValueError('unexpected bridge submodule layout')
    modules.remove_section(section)
    return {section: dict(modules[section]) for section in modules.sections()}


def validate_changes(source, commit, part='all'):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('reuse requires a full source commit')
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', commit, 'HEAD'], check=True)
    for name in git(source, 'diff', '--name-only', commit, 'HEAD').splitlines():
        if name in ALLOWED or name.startswith('tests/'):
            continue
        if part == 'zluda' and name in {PREFIX + 'nvapi-policy-tests.patch', PREFIX + 'build-nvapi-tests.sh'}:
            continue  # Test-only PE fixtures; never read by native compilation.
        if part == 'zluda' and name == 'wine-bridge':
            continue  # No bridge sources are compiled into the native backend.
        if part == 'zluda' and name == '.gitmodules':
            before, after = [git(source, 'show', ref + ':' + name) for ref in [commit, 'HEAD']]
            if native_modules(before) == native_modules(after):
                continue
        if part == 'zluda' and name == PREFIX + 'build-components.sh':
            before, after = [git(source, 'show', ref + ':' + name) for ref in [commit, 'HEAD']]
            if native_recipe(before) == native_recipe(after):
                continue
        if part == 'zluda' and name == PREFIX + 'inputs.lock.json':
            before, after = [json.loads(git(source, 'show', ref + ':' + name))
                             for ref in [commit, 'HEAD']]
            for lock in [before, after]:
                lock.pop('comparison_control', None)
                lock.pop('nvapi_qualification', None)
                lock['components'].pop('wine-bridge', None)
                lock['source_repositories'].pop('wine-bridge', None)
            if before == after:
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
    variable = 'REUSE_ZLUDA_COMMIT' if part == 'zluda' else 'REUSE_BRIDGES_COMMIT'
    commit = os.environ.get(variable)
    if commit:
        return validate_changes(source, commit, part)
    commit = os.environ.get('REUSE_COMPONENTS_COMMIT')
    if commit:
        return validate_changes(source, commit)
    return git(source, 'rev-parse', 'HEAD')


def expected_lock_sha256(source, part='bridges'):
    variable = 'REUSE_ZLUDA_COMMIT' if part == 'zluda' else 'REUSE_BRIDGES_COMMIT'
    if os.environ.get(variable):
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
    elif part == 'zluda':
        required = {'overlay-zluda'}
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
    parser.add_argument('--part', choices=['all', 'bridges', 'zluda'], default='all')
    args = parser.parse_args()
    record = receipt(args.source, json.loads(args.run.read_text()), json.loads(args.artifacts.read_text()), args.part)
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    with open(os.environ['GITHUB_ENV'], 'a') as stream:
        variable = {'bridges': 'REUSE_BRIDGES_COMMIT', 'zluda': 'REUSE_ZLUDA_COMMIT',
                    'all': 'REUSE_COMPONENTS_COMMIT'}[args.part]
        stream.write(variable + '=' + record['commit'] + '\n')
        variable = 'NATIVE_REUSE_RECEIPT' if args.part == 'zluda' else 'COMPONENT_REUSE_RECEIPT'
        stream.write(variable + '=' + str(args.output) + '\n')
