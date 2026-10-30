#!/usr/bin/env python3
"""Hosted Wine CPU gate orchestration; no container launch or GPU backend."""
import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import shutil
import subprocess

import cuda_package
from inputs import HERE, LOCK, git, sha256

MODES = ['off-unset', 'on', 'off-zero', 'on-missing-backend', 'on-relative-backend', 'off-again']
PROFILES = {
    'win64': {'probe': 'probe.exe', 'backend': 'libcuda.so.1', 'machine': 0x8664,
              'magic': 0x20b, 'bits': 64, 'exports': 788, 'table': '__wine_unix_call_funcs',
              'qualification': 'CPU-Wine-PE64-pass; GPU-W1-W2-pending'},
    'wow64': {'probe': 'probe32.exe', 'backend': 'libcuda-wow64.so.1', 'machine': 0x14c,
              'magic': 0x10b, 'bits': 32, 'exports': 690, 'table': '__wine_unix_call_wow64_funcs',
              'qualification': 'CPU-Wine-PE32-WoW64-pass; GPU-W1-W2-pending'},
}
PASS = 'CPU-Wine-PE64-and-PE32-WoW64-pass; GPU-W1-W2-pending'
WOW64_EVENTS = {'backend-loaded': 1, 'cuInit': 2, 'sparse-512MiB-pool': 1,
                'bounded-interior-htod': 1, 'bounded-interior-dtoh': 1,
                'pool-released': 1, 'context-released': 1}


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


def environment(tool, work, wine, server, winearch='win64'):
    if winearch not in PROFILES:
        raise ValueError('unsupported Wine CPU architecture: ' + winearch)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('WINE', 'PROTON_', 'ZLUDA_')) or key in (
                'LD_PRELOAD', 'DISPLAY', 'WAYLAND_DISPLAY', 'CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES'):
            env.pop(key, None)
    # Match ordinary GE runtime paths, excluding bundled lib64 CUDA/ROCm.
    env.update(LD_LIBRARY_PATH=':'.join(str(tool / 'files/lib' / arch)
                                      for arch in ['wine/x86_64-unix', 'x86_64-linux-gnu', 'i386-linux-gnu']),
               WINEPREFIX=str(work / 'prefix'), WINEARCH=winearch, WINEDEBUG='-all',
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
    header = backend.read_bytes()[:20]
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 62:
        raise ValueError('CPU fake backend must be ELF64 x86_64 little-endian')
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


def inspect_probe(probe, winearch):
    info = cuda_package.pe_info(probe, require_builtin=False)
    profile = PROFILES[winearch]
    if (info['machine'], info['optional_magic']) != (profile['machine'], profile['magic']):
        raise ValueError('Wine CPU probe architecture differs from profile: ' + winearch)
    cuda_package.check_pe_imports(info)
    return dict(info, sha256=sha256(probe))


def architecture(winearch):
    profile = PROFILES[winearch]
    return {'winearch': winearch, 'pe_machine': f"0x{profile['machine']:04x}",
            'pe_bits': profile['bits'], 'native_bits': 64, 'expected_exports': profile['exports'],
            'unix_call_table': profile['table']}


def check_wow64(record, results, on_root):
    if on_root is None or not on_root.is_absolute():
        raise ValueError('Wine CPU WoW64 receipt requires the absolute on root')
    for case in record['cases']:
        mode = case['mode']
        expected = {'process_machine': '0x014c', 'native_machine': '0x8664', 'pointer_bits': 32,
                    'module_machine': '0x014c', 'module_bits': 32, 'cu_deviceptr_bits': 32,
                    'exports': 0 if mode.startswith('off') else 690}
        receipt = case.get('architecture')
        if receipt != expected or any(type(receipt[key]) is not type(value) for key, value in expected.items()):
            raise ValueError('Wine CPU PE32 runtime architecture mismatch: ' + mode)
        events = case.get('events')
        wanted = Counter(WOW64_EVENTS) if mode == 'on' else Counter()
        if not isinstance(events, list) or Counter(events) != wanted:
            raise ValueError('Wine CPU WoW64 backend events mismatch: ' + mode)
        event_log = results / (mode + '-backend.log')
        observed = event_log.read_text().splitlines() if event_log.exists() else []
        if Counter(observed) != wanted:
            raise ValueError('Wine CPU WoW64 backend log mismatch: ' + mode)
        witness_file = results / (mode + '-backend.json')
        if mode != 'on':
            if case.get('wow64_witness') is not None or witness_file.exists() or witness_file.is_symlink():
                raise ValueError('Wine CPU off/refused mode has a backend witness: ' + mode)
            continue
        witness = case.get('wow64_witness')
        fixed = {'native_bits': 64, 'unixlib_path': str(on_root / 'x86_64-unix/nvcuda.so'),
                 'unixlib_mapped': True, 'i386_unix_cuda_mapped': False, 'unexpected_cuda_mapping': False,
                 'pool_reserved_bytes': 536870912, 'htod_offset': 260, 'dtoh_offset': 260,
                 'pool_released': True, 'context_released': True}
        if (not isinstance(witness, dict) or set(witness) != set(fixed) | {'native_pid', 'pool_native_base'} or
                any(type(witness.get(key)) is not type(value) or witness[key] != value
                    for key, value in fixed.items()) or
                type(witness.get('native_pid')) is not int or witness['native_pid'] <= 0 or
                type(witness.get('pool_native_base')) is not int or
                not 0xffffffff < witness['pool_native_base'] < 2**64):
            raise ValueError('Wine CPU WoW64 translation witness mismatch')
        if (witness_file.is_symlink() or
                json.dumps(json.loads(witness_file.read_text()), sort_keys=True) != json.dumps(witness, sort_keys=True)):
            raise ValueError('Wine CPU WoW64 retained witness differs from result')


def check_result(results, winearch='win64', on_root=None):
    record = json.loads((results / 'RESULT.json').read_text())
    cleanup = record.get('cleanup', {})
    steps = cleanup.get('steps', [])
    if (record.get('qualification') != PROFILES[winearch]['qualification'] or
            record.get('architecture') != architecture(winearch) or
            [case['mode'] for case in record.get('cases', [])] != MODES or
            any(case['returncode'] for case in record['cases']) or
            cleanup.get('verified_absent') is not True or cleanup.get('lock', {}).get('absent') is not True or
            [step['command'][-1] for step in steps] != ['-k', '-w', '-k0'] or
            any(step.get('accepted') is not True or step['returncode'] not in codes
                for step, codes in zip(steps, [(0, 1), (0,), (1,)]))):
        raise ValueError('Wine CPU runner did not complete all six cases and cleanup for ' + winearch)
    for name in ['bootstrap', *MODES, 'wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']:
        if not (results / (name + '.log')).is_file():
            raise ValueError('Wine CPU runner log missing: ' + name)
    for step, name in zip(steps, ['wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']):
        if step['returncode'] == 1 and (results / (name + '.log')).read_text().strip():
            raise ValueError('Wine CPU runner absence result has diagnostics: ' + name)
    if winearch == 'wow64':
        check_wow64(record, results, on_root)
    return record


def retain_production(tool, work):
    """Keep the exact assembled modules even when a later CPU gate fails."""
    target = work / 'production'
    target.mkdir()
    files = {}
    for name in cuda_package.MODULES:
        source = tool / 'files/lib/wine-cuda/zluda' / name
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        files[name] = sha256(dest)
    contract = tool / 'provenance/ZLUDA-WINE.json'
    shutil.copyfile(contract, target / 'ZLUDA-WINE.json')
    attestation = tool / 'provenance/WINE-CALLBACK-ATTESTATION.json'
    if attestation.is_file():
        shutil.copyfile(attestation, target / attestation.name)
    (target / 'BUILD-CONTRACT.json').write_text(json.dumps(json.loads(contract.read_text())['contract'], indent=2) + '\n')
    (target / 'SHA256SUMS').write_text(''.join(value + '  ' + name + '\n' for name, value in files.items()))
    (target / 'RECEIPT.json').write_text(json.dumps({'kind': 'exact assembled production binaries; not a release artifact',
        'source_pin': LOCK['components']['zluda'], 'files': files, 'provenance_sha256': sha256(contract)}, indent=2) + '\n')


def run(source, tool, work, prepared_fixtures=None):
    if not all(path.is_absolute() for path in [source, tool, work]):
        raise ValueError('Wine CPU paths must be absolute')
    if work.exists():
        raise ValueError('Wine CPU work directory must be fresh')
    if any(work.resolve().is_relative_to(path.resolve()) for path in [source, tool]):
        raise ValueError('Wine CPU outputs must be outside source and tool')
    work.mkdir(parents=True)
    record = {'status': 'running', 'source_pin': LOCK['components']['zluda'],
              'qualification': 'CPU-only gate; real execution pending', 'phases': [], 'profiles': {}}
    failure = None
    wine = server = env = None
    try:
        admit_cpu_devices()
        fixture = admit_source(source)
        wine, server = admit_tool(source, tool)
        retain_production(tool, work)
        contexts = {}
        for winearch in PROFILES:
            directory = work / winearch
            directory.mkdir()
            for name in ['tmp', 'runtime']:
                (directory / name).mkdir(mode=0o700)
            contexts[winearch] = (directory, environment(tool, directory, wine, server, winearch))
        env = contexts['win64'][1]
        build = work / 'build'
        if prepared_fixtures is None:
            compile_env = dict(env, FIXTURE_BUILD_ROOT=str(build))
            if 'WINE_SDK_ROOT' in os.environ:
                compile_env['WINE_SDK_ROOT'] = os.environ['WINE_SDK_ROOT']
            command(['bash', fixture / 'build-fixtures.sh'], compile_env,
                    work / 'compile.log', 180)
            record['phases'].append('compile')
        else:
            import cpu_fixtures
            record['prepared_fixtures'] = cpu_fixtures.admit(source, prepared_fixtures, build)
            (work / 'compile.log').write_text('Admitted fixtures built in the pinned bridges SDK job\n')
            record['phases'].append('prepared-SDK-fixture-admission')
        for winearch, profile in PROFILES.items():
            record['profiles'][winearch] = {
                'status': 'pending', 'prefix': str(contexts[winearch][0] / 'prefix'),
                'fake_backend_needed': inspect_backend(build / profile['backend']),
                'probe': inspect_probe(build / profile['probe'], winearch)}
        for binary, name in [(wine, 'wine'), (server, 'wineserver')]:
            command([binary, '--version'], env, work / (name + '-version.log'), 15)
        for winearch, profile in PROFILES.items():
            directory, env = contexts[winearch]
            report = record['profiles'][winearch]
            report['status'] = 'running'
            error = None
            try:
                command(['python3', HERE / 'pinned_fixture_runner.py', '--runner', fixture / 'run-fixtures.py',
                         '--admission', directory / 'ADMISSION.json', '--wine', wine, '--wineserver', server,
                         '--winearch', winearch, '--dll-dir', tool / 'files/lib/wine',
                         '--on-root', tool / 'files/lib/wine-cuda/zluda',
                         '--off-root', tool / 'files/lib/wine-cuda/stub', '--backend', build / profile['backend'],
                         '--probe', build / profile['probe'], '--prefix', directory / 'prefix',
                         '--results', directory / 'results'], env, directory / 'runner.log', 900)
                report['runner'] = check_result(directory / 'results', winearch, tool / 'files/lib/wine-cuda/zluda')
            except BaseException as caught:
                error = caught
                report['error'] = str(caught)
            finally:
                # Each prefix is checked independently before starting the
                # next profile, also after runner timeout or malformed results.
                if (directory / 'prefix').exists():
                    report['cleanup'] = cleanup_server(server, env, directory)
                    if report['cleanup']['status'] != 'passed':
                        error = error or RuntimeError('Wine CPU outer server cleanup failed: ' + winearch)
                report['status'] = 'failed' if error else 'passed'
            if error:
                raise error
            record['phases'].append('six-case-' + winearch + '-Wine-execution')
    except BaseException as error:
        failure = error
        record['error'] = str(error)
    finally:
        record['case_count'] = sum(len(profile.get('runner', {}).get('cases', []))
                                   for profile in record['profiles'].values())
        if failure is None and (set(record['profiles']) != {'win64', 'wow64'} or record['case_count'] != 12):
            failure = ValueError('Wine CPU gate requires both profiles and twelve admitted cases')
            record['error'] = str(failure)
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
    parser.add_argument('--prepared-fixtures', type=Path)
    parser.add_argument('--a1-tool', type=Path)
    args = parser.parse_args()
    def terminate(signum, frame):
        raise RuntimeError('Wine CPU gate interrupted by signal ' + str(signum))
    signal.signal(signal.SIGTERM, terminate)
    run(args.source, args.tool, args.work, args.prepared_fixtures)
    import lifecycle_cpu
    lifecycle_cpu.run(args.source, args.tool, args.work / 'build', args.work / 'lifecycle')
    import launcher_cpu
    launcher_cpu.run(args.source, args.tool, args.work / 'build', args.work / 'launcher', args.a1_tool if args.a1_tool and (args.a1_tool / 'proton').is_file() else None)
