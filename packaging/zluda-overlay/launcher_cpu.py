#!/usr/bin/env python3
"""Exercise the shipping Proton launcher; never synthesize its DLL namespace."""
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import proton_zluda
from inputs import sha256, PROFILE
import wine_cpu


def fixture_runner(fixture):
    spec = importlib.util.spec_from_file_location('source_wine_cpu_fixtures', fixture / 'run-fixtures.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


def environment(tool, directory, winearch, phase):
    wine, server = [tool / 'files/bin' / name for name in ['wine', 'wineserver']]
    env = wine_cpu.environment(tool, directory, wine, server)
    for key in list(env):
        if key.startswith(('WINE', 'STEAM_', 'PROTON_', 'ZLUDA_')) or key in ('SteamGameId', 'SteamAppId', 'UMU_ID'):
            env.pop(key, None)
    appid = '35140' if winearch == 'wow64' else '999999'
    env.update(HOME=str(directory / 'home'), PWD=str(directory / 'game'),
               STEAM_COMPAT_DATA_PATH=str(directory / 'compatdata'),
               STEAM_COMPAT_CLIENT_INSTALL_PATH=(env.get('CI_STEAM_ROOT', str(directory / 'steam')) if PROFILE['base'] == 'valve' else str(directory / 'steam')),
               STEAM_COMPAT_INSTALL_PATH=str(directory / 'game'),
               STEAM_COMPAT_APP_ID=appid, SteamGameId=appid, SteamAppId=appid,
               UMU_ID=appid, PROTON_LOG='1', PROTON_LOG_DIR=str(directory / phase),
               PROTON_NO_ESYNC='1', PROTON_NO_FSYNC='1', PROTON_USE_WINED3D='1',
               WINEDEBUG='-all', ZLUDA_WINE_FIXTURE_AUDIT=str(directory / phase / 'backend.log'))
    if PROFILE['base'] == 'valve':
        env['WINEDEBUG'] = '-all,err+all'
        if os.environ.get('CI_HEADLESS_DISPLAY') == '1':
            env['DISPLAY'] = os.environ['DISPLAY']
    if winearch == 'wow64':
        env.update(ZLUDA_WINE_FIXTURE_WITNESS=str(directory / phase / 'backend.json'),
                   ZLUDA_WINE_FIXTURE_UNIXLIB=str(tool / 'files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so'))
    if phase.startswith('on'):
        if winearch == 'win64' or PROFILE['base'] == 'valve':
            env['PROTON_ENABLE_ZLUDA'] = '1'
        # WoW64 on deliberately has neither variable: the real 35140 early/main
        # protonfix must set both before Wine initialization / final selection.
    else:
        env['PROTON_ENABLE_ZLUDA'] = '0'
        if winearch == 'wow64':
            env['PROTON_USE_WOW64'] = '1'
    if phase == 'helper':
        env['PROTON_ENABLE_ZLUDA'] = '1'
    if phase in ('off-nvidia', 'on-nvidia'):
        env['PROTON_NVIDIA_NVCUDA'] = '1'
        env['PROTON_NVIDIA_LIBS'] = '1' if phase == 'on-nvidia' else '0'
    return env


def stub_hashes(tool, prefix):
    result = {}
    for directory, arch in proton_zluda.ARCHES.items():
        path = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
        if not path.is_file() or sha256(path) != (proton_zluda.STUB_SHA256 if PROFILE['base'] == 'ge' else PROFILE['stub_artifact']['sha256'])[arch]:
            raise ValueError('launcher prefix did not retain GE stub bytes: ' + str(path))
        result[directory] = sha256(path)
    return result


def admit_case(directory, phase, winearch, tool, runner, returncode):
    output = (directory / ('steam-' + ('35140' if winearch == 'wow64' else '999999') + '.log')).read_text()
    helper = PROFILE['base'] == 'valve' and phase == 'helper'
    if helper:
        expected = {'win64': 'fixture failure at line 75: module!=NULL (last error 126)',
                    'wow64': 'PE32 fixture failure at line 65: module (last error 126)'}[winearch]
        failures = [line for line in output.splitlines() if 'fixture failure at line ' in line]
        if returncode != 1 or failures != [expected] or 'PASS:' in output:
            raise ValueError('Valve helper must refuse nvcuda with exactly error 126')
    elif returncode != 0 or 'PASS:' not in output:
        raise ValueError('real Proton probe failed: ' + str(directory))
    event_file = directory / 'backend.log'
    events = event_file.read_text().splitlines() if event_file.exists() else []
    wanted = (runner.WOW64_EVENTS if winearch == 'wow64' else runner.REQUIRED_EVENTS) if phase.startswith('on') else {}
    if Counter(events) != Counter(wanted):
        raise ValueError('real Proton backend events differ: ' + str(directory))
    record = {'status': 'passed', 'events': events, 'probe_log_sha256': sha256(directory / ('steam-' + ('35140' if winearch == 'wow64' else '999999') + '.log'))}
    if helper:
        record['load_library'] = {'module': 'nvcuda.dll', 'handle': 'NULL', 'last_error': 126, 'probe_exit': returncode}
    if winearch == 'wow64' and not helper:
        record['architecture'] = runner.wow64_receipt(output, 'on' if phase.startswith('on') else 'off-zero')
        if phase.startswith('on'):
            record['wow64_witness'] = runner.wow64_witness(directory / 'backend.json',
                SimpleNamespace(on_root=tool / 'files/lib/wine-cuda/zluda'))
            log = (directory / 'launcher.log').read_text()
            for expected in (['PROTON_USE_WOW64=1', 'PROTON_ENABLE_ZLUDA=1'] if PROFILE['base'] == 'ge' else ['ZLUDA enabled: CUDA-on-HIP translation active']):
                if expected not in log:
                    raise ValueError('real 35140 protonfix did not run: ' + expected)
    return record


PHASES = ['off-before', 'off-a1', 'on-a1', 'off-nvidia', 'on', 'on-local', 'on-prefix', 'on-nvidia', 'off-after']


def install_capture(tool):
    """Capture final game setup; replay Valve targets without the Steam handshake."""
    paths = [tool / 'files/bin/wine']
    if PROFILE['base'] == 'valve':
        paths.append(tool / 'files/lib/wine/x86_64-unix/wine-preloader')
    instrumentation = {}
    for wine in paths:
        original = sha256(wine)
        wine.rename(wine.with_name(wine.name + '-uncaptured'))
        wine.write_text('#!/usr/bin/python3\nVALVE = ' + repr(PROFILE['base'] == 'valve') +
                        '\nTOOL = ' + repr(str(tool)) + '\n' + r'''
import hashlib, json, os, pathlib, sys
path = pathlib.Path(__file__)
argv = list(sys.argv)
game = VALVE and len(argv) >= 4 and argv[2] == r'c:\windows\system32\steam.exe'
if game or any(arg.startswith('Z:') for arg in argv[1:]):
    out = pathlib.Path(os.environ['PROTON_LOG_DIR']) / 'launch-env.json'
    out.write_text(json.dumps(dict(os.environ), indent=2))
    out.with_name('launch-command.json').write_text(json.dumps(argv, indent=2))
    skip = os.environ.get('PROTON_ENABLE_ZLUDA') != '1' and os.environ.get('PROTON_NVIDIA_NVCUDA') == '1'
    if game:
        root = pathlib.Path(TOOL) / 'files/lib/wine/x86_64-unix'
        if ([pathlib.Path(arg) for arg in argv[:2]] != [root / 'wine-preloader', root / 'wine'] or
                os.environ.get('WINELOADERNOEXEC') != '1'):
            raise ValueError('Valve fixture did not retain stock preloader/Steam dispatch')
        # Promote Steam's recorded target, retaining its arguments, env and cwd.
        replay = argv[:2] + argv[3:]
        receipt = {'original_argv': argv, 'replay_argv': replay, 'cwd': os.getcwd(),
                   'environment_sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
                   'replacement': {'removed': argv[2], 'target': argv[3]},
                   'replayed': not skip, 'steam_client_handshake': 'pending Deck qualification'}
        out.with_name('launch-replay.json').write_text(json.dumps(receipt, indent=2))
        argv = replay
    if skip:
        sys.exit(0)
os.execv(str(path.with_name(path.name + '-uncaptured')), argv)
''')
        wine.chmod(0o755)
        instrumentation[wine.relative_to(tool).as_posix()] = {
            'original_sha256': original, 'capture_sha256': sha256(wine)}
    return instrumentation


def admit_capture(directory, tool, arguments, skip=False):
    command = json.loads((directory / 'launch-command.json').read_text())
    env = json.loads((directory / 'launch-env.json').read_text())
    root = tool / 'files/lib/wine/x86_64-unix'
    if (len(command) < 4 or [Path(arg) for arg in command[:2]] != [root / 'wine-preloader', root / 'wine'] or
            command[2] != r'c:\windows\system32\steam.exe' or command[3:] != arguments or
            env.get('WINELOADERNOEXEC') != '1'):
        raise ValueError('Valve fixture did not retain stock preloader/Steam dispatch')
    receipt = json.loads((directory / 'launch-replay.json').read_text())
    if (receipt['original_argv'] != command or receipt['replay_argv'] != command[:2] + command[3:] or
            receipt['replacement'] != {'removed': command[2], 'target': command[3]} or
            receipt['environment_sha256'] != sha256(directory / 'launch-env.json') or
            not Path(receipt['cwd']).is_absolute() or receipt['replayed'] != (not skip) or
            receipt['steam_client_handshake'] != 'pending Deck qualification'):
        raise ValueError('Valve replay differs from the captured command/environment')
    return receipt


def admit_policy(directory, phase, tool, prefix, arguments):
    env = json.loads((directory / 'launch-env.json').read_text())
    dispatch = (admit_capture(directory, tool, arguments, skip=phase == 'off-nvidia')
                if PROFILE['base'] == 'valve' and phase != 'helper' else None)
    on = phase.startswith('on')
    selected = tool / 'files/lib/wine-cuda' / ('zluda' if on else 'stub')
    paths = env['WINEDLLPATH'].split(':')
    if paths[0] != str(selected) or sum('/wine-cuda/' in entry for entry in paths) != 1:
        raise ValueError('real launcher selected the wrong CUDA root')
    overrides = {}
    for clause in env.get('WINEDLLOVERRIDES', '').split(';'):
        names, _, value = clause.partition('=')
        for name in names.split(','):
            overrides[name] = value
    helper = PROFILE['base'] == 'valve' and phase == 'helper'
    if helper:
        if any(name.strip().lower().lstrip('*').removesuffix('.dll') == 'nvcuda' for name in overrides):
            raise ValueError('Valve helper must retain the stock registry load order')
        section, value = None, None
        for line in (prefix / 'user.reg').read_text().splitlines():
            if line.startswith('['):
                section = line.split(']', 1)[0] + ']'
            if section == r'[Software\\Wine\\DllOverrides]' and line.startswith('"nvcuda"='):
                value = line
        if value != '"nvcuda"="disabled"':
            raise ValueError('Valve helper registry must disable nvcuda')
    expected_override = ('n,b' if phase == 'off-nvidia' else 'b') if PROFILE['base'] == 'ge' else ('b' if on else None)
    if (overrides.get('nvcuda') != expected_override and
            not (PROFILE['base'] == 'valve' and not on and overrides.get('nvcuda') == 'b')):
        raise ValueError('real launcher CUDA override differs from GE/on policy')
    if ('ZLUDA_WINE_BACKEND_PATH' in env) != on:
        raise ValueError('backend leaked across launcher mode transition')
    warning = 'ZLUDA is active; skipping GE nvidia-libs nvcuda'
    log = (directory / 'launcher.log').read_text()
    if log.count(warning) != (1 if phase == 'on-nvidia' and PROFILE['base'] == 'ge' else 0):
        raise ValueError('NVIDIA conflict warning count differs')
    files = {}
    for directory_name, arch in proton_zluda.ARCHES.items():
        path = prefix / 'drive_c/windows' / directory_name / 'nvcuda.dll'
        expected = (tool / 'files/lib/wine/nvidia-libs/nvcuda' / arch / 'nvcuda.dll.so'
                    if PROFILE['base'] == 'ge' and phase == 'off-nvidia' and (directory_name == 'system32' or env.get('WINEARCH') != 'wow64')
                    else tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll')
        if not path.is_file() or sha256(path) != sha256(expected):
            raise ValueError('real launcher prefix differs from stock GE/on policy: ' + str(path))
        files[directory_name] = sha256(path)
    if (prefix.parent / 'zluda-cuda-roots-v1.json').exists():
        raise ValueError('launcher wrote obsolete migration marker')
    return {'root': str(selected), 'nvcuda_override': overrides.get('nvcuda'), 'prefix_files': files,
            'warning_count': log.count(warning), 'file_override_only': phase == 'off-nvidia',
            **({'registry_nvcuda': 'disabled'} if helper else {}),
            **({'dispatch': dispatch} if dispatch else {})}




def run(source, tool, fixtures, work):
    if work.exists() or not all(p.is_absolute() for p in [source, tool, fixtures, work]):
        raise ValueError('launcher gate requires absolute paths and fresh outputs')
    work.mkdir(parents=True)
    phases = PHASES + (['helper'] if PROFILE['base'] == 'valve' else [])
    record = {'status': 'running', 'qualification': 'real Proton launcher CPU gate; runtime pending', 'profiles': {}}
    if PROFILE['base'] == 'valve':
        record['steam_client_handshake'] = 'pending Deck qualification; game target capture-and-replay only'
    failure = None
    try:
        wine_cpu.admit_cpu_devices()
        fixture = wine_cpu.admit_source(source)
        wine_cpu.admit_tool(source, tool)
        runner = fixture_runner(fixture)
        disposable = work / 'tool'
        # A full private copy is required: default_pfx is deleted to exercise
        # first-run creation. Only this copy's bundled backend is substituted.
        wine_cpu.command(['cp', '--reflink=auto', '-a', tool, disposable], dict(os.environ), work / 'copy.log', 180)
        install_capture(disposable)
        for winearch, profile in wine_cpu.PROFILES.items():
            directory = work / winearch
            for name in ['tmp', 'runtime', 'home', 'game', 'steam', 'compatdata', *phases]:
                (directory / name).mkdir(parents=True, mode=0o700)
            report = {'status': 'running', 'cases': [], 'prefix': str(directory / 'compatdata/pfx')}
            record['profiles'][winearch] = report
            default = disposable / 'files/share/default_pfx'
            shutil.rmtree(default)
            backend = disposable / 'files/lib64/libcuda.so.1'
            backend.unlink()
            shutil.copyfile(fixtures / profile['backend'], backend)
            if sha256(backend) != sha256(fixtures / profile['backend']):
                raise ValueError('disposable fixture backend copy differs')
            report['disposable_backend_sha256'] = sha256(backend)
            prefix = directory / 'compatdata/pfx'
            if PROFILE['base'] == 'valve': prefix.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixtures / profile['probe'], directory / 'game' / profile['probe'])
            cleanup_env = None
            error = None
            try:
                for phase in [*phases]:
                    if phase.startswith('on') or PROFILE['base'] == 'valve':
                        # CPU fixtures preseed prerequisites; no installer bytes.
                        (prefix / 'winetricks.log').write_text('d3dx9\nd3dcompiler_43\nphysx\n')
                    seeded = {}
                    if phase in ('off-a1', 'on-a1'):
                        # Seed the global Winelib and active PE32 transition layout.
                        for directory_name, arch in proton_zluda.ARCHES.items():
                            path = prefix / 'drive_c/windows' / directory_name / 'nvcuda.dll'
                            path.unlink(missing_ok=True)
                            legacy = disposable / ('files/lib/wine/nvidia-libs/nvcuda/x86_64-windows/nvcuda.dll.so'
                                                    if directory_name == 'system32' and PROFILE['base'] == 'ge' else
                                                    'files/lib/wine-cuda/zluda/i386-windows/nvcuda.dll')
                            if PROFILE['base'] == 'valve' and directory_name == 'system32':
                                legacy = fixtures / 'libcuda.so.1'  # ELF64 layout stand-in, never executed.
                            shutil.copyfile(legacy, path)
                            seeded[directory_name] = sha256(path)
                            expected = disposable / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll'
                            if (seeded[directory_name] == (proton_zluda.STUB_SHA256 if PROFILE['base'] == 'ge' else PROFILE['stub_artifact']['sha256'])[arch] or
                                    seeded[directory_name] == sha256(expected)):
                                raise ValueError('A1 seed already matches transition output: ' + directory_name)
                    local_decoy = directory / 'game/nvcuda.dll'
                    local_decoy.unlink(missing_ok=True)
                    decoy = fixtures / ('nvcuda-decoy32.dll' if winearch == 'wow64' else 'nvcuda-decoy64.dll')
                    if phase == 'on-local':
                        shutil.copyfile(decoy, local_decoy)
                        seeded['game_local_native'] = sha256(local_decoy)
                    if phase == 'on-prefix':
                        path = prefix / 'drive_c/windows' / ('syswow64' if winearch == 'wow64' else 'system32') / 'nvcuda.dll'
                        path.unlink()
                        shutil.copyfile(decoy, path)
                        seeded['prefix_native'] = sha256(path)
                    timestamp = prefix / '.update-timestamp'
                    before_timestamp = None
                    if phase == 'off-after':
                        expected = str(int((disposable / 'files/share/wine/wine.inf').stat().st_mtime))
                        if timestamp.read_text().strip() != expected:
                            raise ValueError('on-to-off fixture requires a current Wine timestamp')
                        before_timestamp = (timestamp.read_bytes(), timestamp.stat().st_mtime_ns)
                    env = environment(disposable, directory, winearch, phase)
                    cleanup_env = wine_cpu.environment(disposable, directory, disposable / 'files/bin/wine',
                                                       disposable / 'files/bin/wineserver', winearch)
                    cleanup_env['WINEPREFIX'] = str(prefix)
                    verb = ('runinprefix' if phase == 'helper' else 'waitforexitandrun'
                            if PROFILE['base'] == 'valve' or winearch == 'wow64' and phase.startswith('on') else 'run')
                    # Windows paths avoid GE's umu.exe; Valve retains steam.exe.
                    target = 'Z:' + str(directory / 'game' / profile['probe']).replace('/', '\\')
                    argv = [disposable / 'proton', verb, target]
                    if not phase.startswith('on'):
                        argv.append('--expect-stub')
                    helper = PROFILE['base'] == 'valve' and phase == 'helper'
                    if helper:
                        from cross_tool_cpu import snapshot
                        tool_before = snapshot(disposable)
                    code = wine_cpu.command(argv, env, directory / phase / 'launcher.log', 240,
                                            accepted=(1,) if helper else (0,))
                    if phase == 'off-nvidia':
                        if code != 0:
                            raise ValueError('native NVIDIA file/override launch failed')
                        result = {'status': 'passed', 'probe_execution': 'skipped: native NVIDIA file/override assertions only'}
                    else:
                        result = admit_case(directory / phase, phase, winearch, disposable, runner, code)
                    result['policy'] = admit_policy(directory / phase, phase, disposable, prefix,
                                                    [str(arg) for arg in argv[2:]])
                    result['seeded_files'] = seeded
                    if phase in ('off-a1', 'on-a1'):
                        result['upgrade_fixture'] = 'A1-layout (stand-in modules)'
                    if phase == 'off-after':
                        if before_timestamp != (timestamp.read_bytes(), timestamp.stat().st_mtime_ns):
                            raise ValueError('on-to-off launch unexpectedly updated the Wine timestamp')
                        result['current_timestamp_unchanged'] = True
                    result.update(phase=phase, command=[str(arg) for arg in argv],
                                  template_stubs=stub_hashes(disposable, default))
                    report['cases'].append(result)
                    # Prevent cross-process fixture constructor/event reuse and
                    # inspect the prefix-specific lock after every launch.
                    cleanup = wine_cpu.cleanup_server(disposable / 'files/bin/wineserver', cleanup_env, directory / phase)
                    result['cleanup'] = cleanup
                    if cleanup['status'] != 'passed':
                        raise ValueError('real launcher left a server: ' + phase)
                    if helper:
                        if snapshot(disposable) != tool_before:
                            raise ValueError('Valve helper mutated the tool payload')
                        result['tool_unchanged'] = True
            except BaseException as caught:
                error = caught
                report['error'] = str(caught)
            finally:
                if cleanup_env is not None:
                    report['cleanup'] = wine_cpu.cleanup_server(disposable / 'files/bin/wineserver', cleanup_env, directory)
                    if report['cleanup']['status'] != 'passed':
                        error = error or ValueError('real launcher cleanup failed: ' + winearch)
                report['status'] = 'failed' if error else 'passed'
            if error:
                raise error
        if set(record['profiles']) != set(wine_cpu.PROFILES) or any(len(p['cases']) != len(phases) for p in record['profiles'].values()):
            raise ValueError('real launcher requires both off/on/off profiles')
        record['qualification'] = 'CPU-Proton-PE64-and-PE32-WoW64-off-on-off-pass; GPU-W1-W2-pending'
    except BaseException as caught:
        failure = caught
        record['error'] = str(caught)
    finally:
        record['status'] = 'failed' if failure else 'passed'
        (work / 'INTEGRATION.json').write_text(json.dumps(record, indent=2) + '\n')
    if failure:
        raise failure
    return record
