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
from inputs import sha256
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
               STEAM_COMPAT_CLIENT_INSTALL_PATH=str(directory / 'steam'),
               STEAM_COMPAT_INSTALL_PATH=str(directory / 'game'),
               STEAM_COMPAT_APP_ID=appid, SteamGameId=appid, SteamAppId=appid,
               UMU_ID=appid, PROTON_LOG='1', PROTON_LOG_DIR=str(directory / phase),
               PROTON_NO_ESYNC='1', PROTON_NO_FSYNC='1', PROTON_USE_WINED3D='1',
               WINEDEBUG='-all', ZLUDA_WINE_FIXTURE_AUDIT=str(directory / phase / 'backend.log'))
    if winearch == 'wow64':
        env.update(ZLUDA_WINE_FIXTURE_WITNESS=str(directory / phase / 'backend.json'),
                   ZLUDA_WINE_FIXTURE_UNIXLIB=str(tool / 'files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so'))
    if phase.startswith('on'):
        if winearch == 'win64':
            env['PROTON_ENABLE_ZLUDA'] = '1'
        # WoW64 on deliberately has neither variable: the real 35140 early/main
        # protonfix must set both before Wine initialization / final selection.
    else:
        env['PROTON_ENABLE_ZLUDA'] = '0'
        if winearch == 'wow64':
            env['PROTON_USE_WOW64'] = '1'
    if phase in ('off-nvidia', 'on-nvidia'):
        env['PROTON_NVIDIA_NVCUDA'] = '1'
        env['PROTON_NVIDIA_LIBS'] = '1' if phase == 'on-nvidia' else '0'
    return env


def stub_hashes(tool, prefix):
    result = {}
    for directory, arch in proton_zluda.ARCHES.items():
        path = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
        if not path.is_file() or sha256(path) != proton_zluda.STUB_SHA256[arch]:
            raise ValueError('launcher prefix did not retain GE stub bytes: ' + str(path))
        result[directory] = sha256(path)
    return result


def admit_case(directory, phase, winearch, tool, runner, returncode):
    output = (directory / ('steam-' + ('35140' if winearch == 'wow64' else '999999') + '.log')).read_text()
    if returncode != 0 or 'PASS:' not in output:
        raise ValueError('real Proton probe failed: ' + str(directory))
    event_file = directory / 'backend.log'
    events = event_file.read_text().splitlines() if event_file.exists() else []
    wanted = (runner.WOW64_EVENTS if winearch == 'wow64' else runner.REQUIRED_EVENTS) if phase.startswith('on') else {}
    if Counter(events) != Counter(wanted):
        raise ValueError('real Proton backend events differ: ' + str(directory))
    record = {'status': 'passed', 'events': events, 'probe_log_sha256': sha256(directory / ('steam-' + ('35140' if winearch == 'wow64' else '999999') + '.log'))}
    if winearch == 'wow64':
        record['architecture'] = runner.wow64_receipt(output, 'on' if phase.startswith('on') else 'off-zero')
        if phase.startswith('on'):
            record['wow64_witness'] = runner.wow64_witness(directory / 'backend.json',
                SimpleNamespace(on_root=tool / 'files/lib/wine-cuda/zluda'))
            log = (directory / 'launcher.log').read_text()
            for expected in ['PROTON_USE_WOW64=1', 'PROTON_ENABLE_ZLUDA=1']:
                if expected not in log:
                    raise ValueError('real 35140 protonfix did not run: ' + expected)
    return record


PHASES = ['off-before', 'off-a1', 'on-a1', 'off-nvidia', 'on', 'on-local', 'on-prefix', 'on-nvidia', 'off-after']


def install_capture(tool):
    """Observe the final real launcher environment at Wine's process boundary.

    The native-NVIDIA off case exits before loading the relay: only its prefix
    files/overrides are under test on this CPU-only runner.
    """
    wine = tool / 'files/bin/wine'
    wine.rename(wine.with_name('wine-uncaptured'))
    wine.write_text('''#!/usr/bin/python3
import json, os, pathlib, sys
if any(arg.startswith('Z:') for arg in sys.argv[1:]):
    out = pathlib.Path(os.environ['PROTON_LOG_DIR']) / 'launch-env.json'
    out.write_text(json.dumps(dict(os.environ), indent=2))
    if os.environ.get('PROTON_ENABLE_ZLUDA') != '1' and os.environ.get('PROTON_NVIDIA_NVCUDA') == '1':
        sys.exit(0)
os.execv(str(pathlib.Path(__file__).with_name('wine-uncaptured')), sys.argv)
''')
    wine.chmod(0o755)


def admit_policy(directory, phase, tool, prefix):
    env = json.loads((directory / 'launch-env.json').read_text())
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
    if overrides.get('nvcuda') != ('n,b' if phase == 'off-nvidia' else 'b'):
        raise ValueError('real launcher CUDA override differs from GE/on policy')
    if ('ZLUDA_WINE_BACKEND_PATH' in env) != on:
        raise ValueError('backend leaked across launcher mode transition')
    warning = 'ZLUDA is active; skipping GE nvidia-libs nvcuda'
    log = (directory / 'launcher.log').read_text()
    if log.count(warning) != (1 if phase == 'on-nvidia' else 0):
        raise ValueError('NVIDIA conflict warning count differs')
    files = {}
    for directory_name, arch in proton_zluda.ARCHES.items():
        path = prefix / 'drive_c/windows' / directory_name / 'nvcuda.dll'
        expected = (tool / 'files/lib/wine/nvidia-libs/nvcuda' / arch / 'nvcuda.dll.so'
                    if phase == 'off-nvidia' and (directory_name == 'system32' or env.get('WINEARCH') != 'wow64')
                    else tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll')
        if not path.is_file() or sha256(path) != sha256(expected):
            raise ValueError('real launcher prefix differs from stock GE/on policy: ' + str(path))
        files[directory_name] = sha256(path)
    if (prefix.parent / 'zluda-cuda-roots-v1.json').exists():
        raise ValueError('launcher wrote obsolete migration marker')
    return {'root': str(selected), 'nvcuda_override': overrides['nvcuda'], 'prefix_files': files,
            'warning_count': log.count(warning), 'file_override_only': phase == 'off-nvidia'}


def create_a1_prefix(tool, directory, winearch):
    """Optional historical artifact gate; never called by durable default CI."""
    phase = directory / 'a1-create'
    phase.mkdir()
    env = environment(tool, directory, winearch, 'a1-create')
    env['PROTONFIXES_DISABLE'] = '1'
    code = wine_cpu.command([tool / 'proton', 'run', r'C:\windows\system32\cmd.exe', '/c', 'exit', '0'],
                            env, phase / 'launcher.log', 240)
    cleanup = wine_cpu.environment(tool, directory, tool / 'files/bin/wine', tool / 'files/bin/wineserver', winearch)
    cleanup['WINEPREFIX'] = str(directory / 'compatdata/pfx')
    cleanup_record = wine_cpu.cleanup_server(tool / 'files/bin/wineserver', cleanup, phase)
    if code or cleanup_record['status'] != 'passed':
        raise ValueError('historical A1 launcher creation/cleanup failed')
    record = {'label': 'CI-built A1 (run 40906607864), not the Deck-installed bytes',
              'modules': {}, 'prefix': {}, 'template': {}}
    for name in ['x86_64-windows/nvcuda.dll', 'i386-windows/nvcuda.dll',
                 'x86_64-unix/nvcuda.dll.so', 'x86_64-unix/nvcuda.so']:
        record['modules'][name] = sha256(tool / 'files/lib/wine' / name)
    for directory_name in proton_zluda.ARCHES:
        for key, prefix in [('prefix', directory / 'compatdata/pfx'),
                            ('template', tool / 'files/share/default_pfx')]:
            path = prefix / 'drive_c/windows' / directory_name / 'nvcuda.dll'
            record[key][directory_name] = {'sha256': sha256(path),
                'symlink': os.readlink(path) if path.is_symlink() else None}
    record['cleanup'] = cleanup_record
    (phase / 'A1.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def run(source, tool, fixtures, work, a1_tool=None):
    if work.exists() or not all(p.is_absolute() for p in [source, tool, fixtures, work]):
        raise ValueError('launcher gate requires absolute paths and fresh outputs')
    work.mkdir(parents=True)
    record = {'status': 'running', 'qualification': 'real Proton launcher CPU gate; runtime pending', 'profiles': {}}
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
        if a1_tool is not None:
            private_a1 = work / 'a1-tool'
            wine_cpu.command(['cp', '--reflink=auto', '-a', a1_tool, private_a1], dict(os.environ), work / 'a1-copy.log', 180)
            a1_tool = private_a1
        for winearch, profile in wine_cpu.PROFILES.items():
            directory = work / winearch
            for name in ['tmp', 'runtime', 'home', 'game', 'steam', 'compatdata', *PHASES]:
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
            if a1_tool is not None:
                report['historical_a1'] = create_a1_prefix(a1_tool, directory, winearch)
            prefix = directory / 'compatdata/pfx'
            shutil.copyfile(fixtures / profile['probe'], directory / 'game' / profile['probe'])
            cleanup_env = None
            error = None
            try:
                for phase in [*PHASES]:
                    if phase.startswith('on'):
                        # Avoid downloads/installers in the actual Asylum fix.
                        (prefix / 'winetricks.log').write_text('d3dx9\nd3dcompiler_43\nphysx\n')
                    seeded = {}
                    if phase in ('off-a1', 'on-a1'):
                        # Published A1 layout: global Winelib x64 and builtin
                        # PE32 prefix copies. Exact historical bytes, if supplied,
                        # can replace this layout fixture without policy changes.
                        for directory_name, arch in proton_zluda.ARCHES.items():
                            path = prefix / 'drive_c/windows' / directory_name / 'nvcuda.dll'
                            path.unlink(missing_ok=True)
                            legacy = ((a1_tool / 'files/lib/wine/x86_64-unix/nvcuda.dll.so' if a1_tool else disposable / 'files/lib/wine/nvidia-libs/nvcuda/x86_64-windows/nvcuda.dll.so')
                                      if directory_name == 'system32' else
                                      (a1_tool / 'files/lib/wine/i386-windows/nvcuda.dll' if a1_tool else disposable / 'files/lib/wine-cuda/zluda/i386-windows/nvcuda.dll'))
                            shutil.copyfile(legacy, path)
                            seeded[directory_name] = sha256(path)
                            expected = disposable / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll'
                            if (seeded[directory_name] == proton_zluda.STUB_SHA256[arch] or
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
                    verb = 'waitforexitandrun' if winearch == 'wow64' and phase.startswith('on') else 'run'
                    # A Windows path takes Proton's normal non-Steam Wine entry,
                    # avoiding umu.exe / Steam itself, with the shipping script.
                    target = 'Z:' + str(directory / 'game' / profile['probe']).replace('/', '\\')
                    argv = [disposable / 'proton', verb, target]
                    if not phase.startswith('on'):
                        argv.append('--expect-stub')
                    code = wine_cpu.command(argv, env, directory / phase / 'launcher.log', 240)
                    if phase == 'off-nvidia':
                        if code != 0:
                            raise ValueError('native NVIDIA file/override launch failed')
                        result = {'status': 'passed', 'probe_execution': 'skipped: native NVIDIA file/override assertions only'}
                    else:
                        result = admit_case(directory / phase, phase, winearch, disposable, runner, code)
                    result['policy'] = admit_policy(directory / phase, phase, disposable, prefix)
                    result['seeded_files'] = seeded
                    if phase in ('off-a1', 'on-a1'):
                        result['upgrade_fixture'] = ('CI-built A1 (run 40906607864), not the Deck-installed bytes'
                                                     if a1_tool else 'A1-layout (stand-in modules)')
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
        if set(record['profiles']) != set(wine_cpu.PROFILES) or any(len(p['cases']) != len(PHASES) for p in record['profiles'].values()):
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
