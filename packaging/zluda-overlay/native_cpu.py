#!/usr/bin/env python3
"""Run the full native harness's CPU cases and retain its SDK loader closure."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from inputs import LOCK, LOCK_FILE, git, packaging_identity, sha256


def run():
    source = Path(os.environ['SOURCE_ROOT']) / 'zluda'
    sdk = Path(os.environ['SDK_ROOT'])
    output = Path(os.environ['BUILD_ROOT']) / 'native-cpu'
    output.mkdir()
    if any(Path('/dev', name).exists() for name in ('kfd', 'dri')):
        raise ValueError('native CPU qualification must not expose GPU devices')
    if git(source, 'rev-parse', 'HEAD') != LOCK['components']['zluda']:
        raise ValueError('native harness source differs from the backend pin')
    command = ['cargo', 'test', '--release', '--locked', '--offline', '-j3', '-p', 'zluda', '--lib',
               '--message-format=json', 'r#impl::context::tests::destroy_context_', '--', '--test-threads=1']
    harnesses = set()
    lines = []
    with (output / 'CPU.log').open('w') as log:
        process = subprocess.Popen(command, cwd=source, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            log.write(line)
            print(line, end='', flush=True)
            lines.append(line)
            if line.startswith('{'):
                item = json.loads(line)
                if (item.get('reason') == 'compiler-artifact' and item.get('profile', {}).get('test') and
                        item.get('target', {}).get('name') == 'nvcuda' and item.get('executable')):
                    harnesses.add(Path(item['executable']))
        if process.wait() or len(harnesses) != 1:
            raise ValueError('native harness compilation or CPU tests failed')
    tests = re.findall(r'^test (r#impl::context::tests::destroy_context_\w+) \.\.\. ok$', ''.join(lines), re.M)
    if len(set(tests)) != 3 or not re.search(r'test result: ok\. 3 passed; 0 failed; 0 ignored;', ''.join(lines)):
        raise ValueError('native qualification requires all three real CPU cases')
    harness = harnesses.pop()
    shutil.copy2(harness, output / 'native-tests')
    runtime = output / 'runtime'
    runtime.mkdir()
    libraries = {}

    def retain(path, name=None):
        name = name or path.name
        digest = sha256(path)
        if name in libraries and libraries[name]['sha256'] != digest:
            raise ValueError('ambiguous native dependency: ' + name)
        shutil.copy2(path.resolve(), runtime / name)
        libraries[name] = {'source': str(path), 'sha256': digest}

    targets = [harness]
    for real, aliases in LOCK['rocm_runtime'].items():
        path = sdk / 'lib' / real
        for name in [real, *aliases]:
            retain(path, name)
        targets.append(path)
    with (output / 'LOADER.log').open('w') as log:
        for target in targets:
            result = subprocess.run(['ldd', str(target)], text=True, capture_output=True, check=True)
            log.write(str(target) + '\n' + result.stdout + result.stderr)
            if 'not found' in result.stdout:
                raise ValueError('incomplete native harness dependency closure')
            for name, path in re.findall(r'^\s*(\S+) => (/\S+) \(', result.stdout, re.M):
                retain(Path(path), name)
    elf = subprocess.check_output(['readelf', '-l', str(harness)], text=True)
    loader = Path(re.search(r'Requesting program interpreter: ([^\]]+)\]', elf)[1])
    retain(loader)
    entry = output / 'run.sh'
    entry.write_text('#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                     'exec "$root/runtime/' + loader.name + '" --library-path "$root/runtime" "$root/native-tests" "$@"\n')
    entry.chmod(0o755)
    with (output / 'TESTS.txt').open('w') as listing:
        subprocess.run([str(entry), '--list'], stdout=listing, check=True)
    record = {'status': 'passed', 'backend': LOCK['components']['zluda'], 'llvm': LOCK['llvm'],
              'sdk_image': LOCK['sdk_image'], 'rust': LOCK['rust_version'], 'packaging': packaging_identity(),
              'input_lock_sha256': sha256(LOCK_FILE), 'command': command, 'cpu_tests': tests,
              'harness_sha256': sha256(output / 'native-tests'), 'loader': loader.name, 'libraries': libraries,
              'gpu_execution': False, 'qualification': 'three CPU cases in the full native crate; GPU cases pending'}
    (output / 'RESULT.json').write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    run()
