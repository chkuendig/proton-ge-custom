#!/usr/bin/env python3
"""Hosted Wine CPU gate orchestration; no container launch or GPU backend."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess

import cuda_package
from inputs import HERE, LOCK, git, sha256

MODES = ['off-unset', 'on', 'off-zero', 'on-missing-backend', 'on-relative-backend', 'off-again']
PASS = 'CPU-Wine-PE64-pass; GPU-W1-W2-pending'


def admit_source(source):
    pin = LOCK['components']['zluda']
    child = source / 'zluda'
    if git(source, 'rev-parse', 'HEAD:zluda') != pin or git(child, 'rev-parse', 'HEAD') != pin:
        raise ValueError('Wine CPU source gitlink differs from input lock')
    if git(child, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=all'):
        raise ValueError('dirty Wine CPU source')
    bridge = LOCK['wine_bridge']
    if git(child, 'rev-parse', 'HEAD:' + bridge['path']) != bridge['tree']:
        raise ValueError('Wine CPU crate tree differs from input lock')
    if sha256(child / bridge['path'] / 'Cargo.lock') != bridge['cargo_lock_sha256']:
        raise ValueError('Wine CPU crate lock differs from input lock')
    return child / bridge['path'] / 'tests/abi'


def admit_tool(source, tool):
    record = json.loads((tool / 'provenance/PROVENANCE.json').read_text())
    if (record['proton'] != git(source, 'rev-parse', 'HEAD') or
            record['input_lock_sha256'] != sha256(HERE / 'inputs.lock.json') or
            record['components']['zluda'] != LOCK['components']['zluda']):
        raise ValueError('Wine CPU assembled tool provenance differs from source')
    cuda_package.audit(tool)
    binaries = [tool / 'files/bin' / name for name in ['wine', 'wineserver']]
    for binary in binaries:
        if not binary.resolve().is_relative_to(tool.resolve()) or not os.access(binary, os.X_OK):
            raise ValueError('missing/non-tool Wine CPU executable: ' + str(binary))
    return binaries


def environment(tool, work, wine, server):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('WINE', 'PROTON_', 'ZLUDA_')) or key in (
                'LD_PRELOAD', 'DISPLAY', 'WAYLAND_DISPLAY', 'CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES'):
            env.pop(key, None)
    # Match ordinary GE runtime paths, excluding bundled lib64 CUDA/ROCm.
    env.update(LD_LIBRARY_PATH=':'.join(str(tool / 'files/lib' / arch)
                                      for arch in ['x86_64-linux-gnu', 'i386-linux-gnu']),
               WINEPREFIX=str(work / 'prefix'), WINEARCH='win64', WINEDEBUG='-all',
               WINELOADER=str(wine), WINESERVER=str(server), WINEESYNC='0', WINEFSYNC='0',
               PATH=str(tool / 'files/bin') + ':' + env.get('PATH', '/usr/bin:/bin'),
               LC_ALL='C', TMPDIR=str(work / 'tmp'), XDG_RUNTIME_DIR=str(work / 'runtime'))
    return env


def command(argv, env, log, timeout, accepted=(0,)):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('w') as output:
        result = subprocess.run([str(arg) for arg in argv], env=env, stdout=output,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode not in accepted:
        raise RuntimeError(f'Wine CPU command failed ({result.returncode}): {log}')
    return result.returncode


def cleanup_server(server, env, work):
    # Wine main.c exits !kill_lock_owner(): -k returns 1 when already gone.
    # Require the lock wait, then signal-0 probe too: a surviving server returns
    # 0 for -k0. An error diagnostic must never masquerade as an absent server.
    record = {'status': 'passed', 'commands': []}
    for flag, label in [('-k', 'kill'), ('-w', 'wait'), ('-k0', 'absent')]:
        log = work / ('outer-server-' + label + '.log')
        entry = {'command': [str(server), flag], 'log': log.name}
        record['commands'].append(entry)
        try:
            code = command([server, flag], env, log, 15, accepted=(0, 1))
            entry['returncode'] = code
            if ((flag == '-w' and code != 0) or (flag == '-k0' and code != 1) or
                    (code == 1 and log.read_text().strip())):
                raise RuntimeError('Wine CPU server cleanup failed or server survived: ' + str(log))
        except BaseException as error:
            record['status'] = 'failed'
            entry['error'] = str(error)
    try:
        record['lock'] = server_lock_state(Path(env['WINEPREFIX']))
    except BaseException as error:
        record['status'] = 'failed'
        record['lock'] = {'error': str(error)}
    return record


def server_lock_state(prefix, temporary=Path('/tmp')):
    """Independently verify Wine's byte-0 server lock, without creating files."""
    try:
        info = prefix.lstat()
    except FileNotFoundError:
        return {'state': 'absent-prefix', 'path': str(prefix)}
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('Wine CPU prefix must be an owned regular directory')
    parts = ['.wine-' + str(os.getuid()), f'server-{info.st_dev:x}-{info.st_ino:x}', 'lock']
    path = temporary.joinpath(*parts)
    result = {'path': str(path)}
    directory = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        for name in parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
            if os.fstat(directory).st_uid != os.getuid():
                raise ValueError('Wine CPU server directory has another owner')
        lock = os.open(parts[-1], os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('Wine CPU server lock must be an owned regular file')
        # Wine request.c uses this same POSIX byte-range lock. A held lock or
        # any filesystem/locking error must fail, even if -w exited zero.
        fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0, os.SEEK_SET)
        result['state'] = 'unlocked'
    except FileNotFoundError:
        result['state'] = 'absent-lock-path'
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)
    return result


def admit_cpu_devices(dev=Path('/dev')):
    if any((dev / name).exists() for name in ['dri', 'kfd']):
        raise ValueError('Wine CPU gate must not expose GPU devices')


def inspect_backend(backend):
    if backend.read_bytes()[:6] != b'\x7fELF\x02\x01':
        raise ValueError('CPU fake backend must be ELF64 little-endian')
    dynamic = subprocess.check_output(['readelf', '--wide', '--dynamic', str(backend)], text=True)
    if re.findall(r'\(SONAME\).*?\[(.*?)\]', dynamic) != ['libcuda.so.1']:
        raise ValueError('CPU fake backend SONAME differs from fixture contract')
    needed = re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic)
    allowed = {'libc.so.6', 'libpthread.so.0', 'ld-linux-x86-64.so.2'}
    if not set(needed) <= allowed:
        raise ValueError('CPU fake backend has unexpected dependencies: ' + ', '.join(needed))
    if re.search(r'\((?:RPATH|RUNPATH)\)', dynamic):
        raise ValueError('CPU fake backend must have no runtime search path')
    return needed


def check_result(results):
    record = json.loads((results / 'RESULT.json').read_text())
    cleanup = record.get('cleanup', {})
    steps = cleanup.get('steps', [])
    if (record.get('qualification') != PASS or
            [case['mode'] for case in record.get('cases', [])] != MODES or
            any(case['returncode'] for case in record['cases']) or
            cleanup.get('verified_absent') is not True or cleanup.get('lock', {}).get('absent') is not True or
            [step['command'][-1] for step in steps] != ['-k', '-w', '-k0'] or
            any(step.get('accepted') is not True or step['returncode'] not in codes
                for step, codes in zip(steps, [(0, 1), (0,), (1,)]))):
        raise ValueError('Wine CPU runner did not complete all six cases and cleanup')
    for name in ['bootstrap', *MODES, 'wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']:
        if not (results / (name + '.log')).is_file():
            raise ValueError('Wine CPU runner log missing: ' + name)
    for step, name in zip(steps, ['wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']):
        if step['returncode'] == 1 and (results / (name + '.log')).read_text().strip():
            raise ValueError('Wine CPU runner absence result has diagnostics: ' + name)
    return record


def run(source, tool, work):
    if not all(path.is_absolute() for path in [source, tool, work]):
        raise ValueError('Wine CPU paths must be absolute')
    if work.exists():
        raise ValueError('Wine CPU work directory must be fresh')
    if any(work.resolve().is_relative_to(path.resolve()) for path in [source, tool]):
        raise ValueError('Wine CPU outputs must be outside source and tool')
    work.mkdir(parents=True)
    record = {'status': 'running', 'source_pin': LOCK['components']['zluda'],
              'qualification': 'CPU-only gate; real execution pending', 'phases': []}
    failure = None
    wine = server = env = None
    try:
        admit_cpu_devices()
        fixture = admit_source(source)
        wine, server = admit_tool(source, tool)
        for name in ['tmp', 'runtime']:
            (work / name).mkdir(mode=0o700)
        env = environment(tool, work, wine, server)
        build = work / 'build'
        command(['bash', fixture / 'build-fixtures.sh'], dict(env, FIXTURE_BUILD_ROOT=str(build)),
                work / 'compile.log', 180)
        record['phases'].append('compile')
        record['fake_backend_needed'] = inspect_backend(build / 'libcuda.so.1')
        for binary, name in [(wine, 'wine'), (server, 'wineserver')]:
            command([binary, '--version'], env, work / (name + '-version.log'), 15)
        command(['python3', fixture / 'run-fixtures.py', '--wine', wine, '--wineserver', server,
                 '--dll-dir', tool / 'files/lib/wine', '--on-root', tool / 'files/lib/wine-cuda/zluda',
                 '--off-root', tool / 'files/lib/wine-cuda/stub', '--backend', build / 'libcuda.so.1',
                 '--probe', build / 'probe.exe', '--prefix', work / 'prefix', '--results', work / 'results'],
                env, work / 'runner.log', 900)
        record['runner'] = check_result(work / 'results')
        record['phases'].append('six-case-Wine-execution')
    except BaseException as error:
        failure = error
        record['error'] = str(error)
    finally:
        # The runner cleans up too; this outer guard covers timeout/early errors.
        # Prefix-local -k/-w cannot affect another Wine server or prefix.
        if env is not None and (work / 'prefix').exists():
            record['cleanup'] = cleanup_server(server, env, work)
            if record['cleanup']['status'] != 'passed':
                failure = failure or RuntimeError('Wine CPU outer server cleanup failed; see INTEGRATION.json')
        record['status'] = 'failed' if failure else 'passed'
        if not failure:
            record['qualification'] = PASS
        (work / 'INTEGRATION.json').write_text(json.dumps(record, indent=2) + '\n')
    if failure:
        raise failure
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'tool', 'work']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    def terminate(signum, frame):
        raise RuntimeError('Wine CPU gate interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, terminate)
    run(args.source, args.tool, args.work)
