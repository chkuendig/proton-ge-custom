#!/usr/bin/env python3
"""Retain the full native test harness and its SDK runtime for Deck qualification."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()


root, build = map(Path, sys.argv[1:])
source, native, deps = build / 'src-zluda', build / 'obj-zluda', build / 'zluda-deps'
output = build / 'native-tests'
output.mkdir()
spec = json.loads((Path(__file__).with_name('native-cases.json')).read_text())
lock = json.loads((root / 'make/zluda-deps.json').read_text())
assert git(source, 'rev-parse', 'HEAD') == spec['backend']
assert os.environ['SDK'] == 'registry.gitlab.steamos.cloud/proton/steamrt4/sdk/x86_64@sha256:97526b794ce1a9bed5f891084462260b3a02399569f7438a3a57b5a253001db9'
assert not any(Path('/dev', name).exists() for name in ('kfd', 'dri'))
rocm = deps / ('sdk/opt/rocm-' + lock['rocm_version'])
env = dict(os.environ, SOURCE_DATE_EPOCH='1793802357', CARGO_BUILD_JOBS='3',
           CMAKE_BUILD_PARALLEL_LEVEL='3', CARGO_TARGET_DIR=str(native / 'target'),
           ZLUDA_HIP_SDK_ROOT=str(rocm), ZLUDA_REAL_CMAKE=shutil.which('cmake'),
           CMAKE=str(root / 'make/zluda-cmake.py'), ZLUDA_BUILD_SOURCE=str(source),
           ZLUDA_MATHCOMPAT_OBJECT=str(native / 'mathcompat.o'), SCCACHE_PATH=shutil.which('ccache'),
           LIBRARY_PATH=str(native / 'stubs') + ':' + str(rocm / 'lib'),
           LD_LIBRARY_PATH=str(build / 'dist/files/lib64') + ':' + str(rocm / 'lib'),
           PATH=str(deps / 'rust/bin') + ':' + os.environ['PATH'])
env.pop('DESTDIR', None)
command = ['cargo', 'test', '--release', '--locked', '--offline', '-j3', '-p', 'zluda', '--lib',
           '--message-format=json', 'r#impl::context::tests::destroy_context_', '--', '--test-threads=1']
harnesses, lines = set(), []
with (output / 'CPU.log').open('w') as log:
    process = subprocess.Popen(command, cwd=source, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        log.write(line)
        lines.append(line)
        if line.startswith('{'):
            item = json.loads(line)
            if (item.get('reason') == 'compiler-artifact' and item.get('profile', {}).get('test') and
                    item.get('target', {}).get('name') == 'nvcuda' and item.get('executable')):
                harnesses.add(Path(item['executable']))
            if item.get('message', {}).get('rendered'):
                print(item['message']['rendered'], end='', flush=True)
        else:
            print(line, end='', flush=True)
    assert process.wait() == 0 and len(harnesses) == 1, 'native harness compilation/CPU failure'
tests = re.findall(r'^test (r#impl::context::tests::destroy_context_\w+) \.\.\. ok$', ''.join(lines), re.M)
assert len(set(tests)) == 3 and 'test result: ok. 3 passed; 0 failed; 0 ignored;' in ''.join(lines)
harness = harnesses.pop()
shutil.copy2(harness, output / 'native-tests')
runtime, libraries = output / 'runtime', {}
runtime.mkdir()


def retain(path, name=None):
    name = name or path.name
    assert not path.resolve().is_relative_to(native / 'stubs'), 'build-only link stub in runtime'
    digest = sha(path)
    assert name not in libraries or libraries[name]['sha256'] == digest, 'ambiguous dependency: ' + name
    shutil.copy2(path.resolve(), runtime / name)
    libraries[name] = {'source': str(path), 'sha256': digest}


targets = [harness]
for real, aliases in lock['runtime'].items():
    path = rocm / 'lib' / real
    for name in [real, *aliases]:
        retain(path, name)
    targets.append(path)
with (output / 'LOADER.log').open('w') as log:
    for target in targets:
        result = subprocess.run(['ldd', str(target)], env=env, text=True, capture_output=True, check=True)
        log.write(str(target) + '\n' + result.stdout + result.stderr)
        assert 'not found' not in result.stdout + result.stderr, 'incomplete native runtime closure'
        for name, path in re.findall(r'^\s*(\S+) => (/\S+) \(', result.stdout, re.M):
            retain(Path(path), name)
elf = subprocess.check_output(['readelf', '-l', str(harness)], text=True)
loader = Path(re.search(r'Requesting program interpreter: ([^\]]+)\]', elf)[1])
retain(loader)
entry = output / 'run.sh'
entry.write_text('#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
                 'exec "$root/runtime/' + loader.name + '" --library-path "$root/runtime" "$root/native-tests" "$@"\n')
entry.chmod(0o755)
listing = subprocess.check_output([str(entry), '--list'], text=True)
(output / 'TESTS.txt').write_text(listing)
available = {line.removesuffix(': test') for line in listing.splitlines() if line.endswith(': test')}
expected = [case for cases in spec['modules'].values() for case in cases]
assert len(expected) == len(set(expected)) == 33 and set(expected) <= available
replay = subprocess.check_output([str(entry), 'r#impl::context::tests::destroy_context_', '--test-threads=1'], text=True)
assert 'test result: ok. 3 passed; 0 failed; 0 ignored;' in replay
(output / 'CPU-REPLAY.log').write_text(replay)
print(replay, end='', flush=True)
shutil.copy2(Path(__file__).with_name('native-cases.json'), output / 'GPU-TESTS.json')
record = dict(status='passed', backend=spec['backend'], llvm=git(source / 'ext/llvm-project', 'rev-parse', 'HEAD'),
              proton=git(root, 'rev-parse', 'HEAD'), sdk_image=os.environ['SDK'], rust=lock['rust_version'],
              input_lock_sha256=sha(root / 'make/zluda-deps.json'), command=command, cpu_tests=tests,
              retained_runtime_cpu_replay=True, harness_sha256=sha(output / 'native-tests'),
              native_backend_sha256=sha(build / 'dist/files/lib64/libcuda.so.1'), loader=loader.name,
              libraries=libraries, gpu_cases_present=expected, gpu_execution=False)
(output / 'RESULT.json').write_text(json.dumps(record, indent=2) + '\n')
files = sorted(path for path in output.rglob('*') if path.is_file())
(output / 'MANIFEST').write_text(''.join(sha(path) + '  ' + path.relative_to(output).as_posix() + '\n' for path in files))
print(json.dumps({key: record[key] for key in ('status', 'backend', 'harness_sha256', 'gpu_execution')}))
