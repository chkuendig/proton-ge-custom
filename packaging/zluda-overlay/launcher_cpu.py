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
    if phase == 'on':
        if winearch == 'win64':
            env['PROTON_ENABLE_ZLUDA'] = '1'
        # WoW64 on deliberately has neither variable: the real 35140 early/main
        # protonfix must set both before Wine initialization / final selection.
    else:
        env['PROTON_ENABLE_ZLUDA'] = '0'
        if winearch == 'wow64':
            env['PROTON_USE_WOW64'] = '1'
    return env


def stub_hashes(tool, prefix):
    result = {}
    for directory, arch in proton_zluda.ARCHES.items():
        path = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
        if path.is_symlink() or not path.is_file() or sha256(path) != proton_zluda.STUB_SHA256[arch]:
            raise ValueError('launcher prefix did not retain regular GE stub bytes: ' + str(path))
        result[directory] = sha256(path)
    return result


def admit_case(directory, phase, winearch, tool, runner, returncode):
    output = (directory / ('steam-' + ('35140' if winearch == 'wow64' else '999999') + '.log')).read_text()
    if returncode != 0 or 'PASS:' not in output:
        raise ValueError('real Proton probe failed: ' + str(directory))
    event_file = directory / 'backend.log'
    events = event_file.read_text().splitlines() if event_file.exists() else []
    wanted = (runner.WOW64_EVENTS if winearch == 'wow64' else runner.REQUIRED_EVENTS) if phase == 'on' else {}
    if Counter(events) != Counter(wanted):
        raise ValueError('real Proton backend events differ: ' + str(directory))
    record = {'status': 'passed', 'events': events, 'probe_log_sha256': sha256(directory / ('steam-' + ('35140' if winearch == 'wow64' else '999999') + '.log'))}
    if winearch == 'wow64':
        record['architecture'] = runner.wow64_receipt(output, 'on' if phase == 'on' else 'off-zero')
        if phase == 'on':
            record['wow64_witness'] = runner.wow64_witness(directory / 'backend.json',
                SimpleNamespace(on_root=tool / 'files/lib/wine-cuda/zluda'))
            log = (directory / 'launcher.log').read_text()
            for expected in ['PROTON_USE_WOW64=1', 'PROTON_ENABLE_ZLUDA=1']:
                if expected not in log:
                    raise ValueError('real 35140 protonfix did not run: ' + expected)
    return record


def run(source, tool, fixtures, work):
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
        for winearch, profile in wine_cpu.PROFILES.items():
            directory = work / winearch
            for name in ['tmp', 'runtime', 'home', 'game', 'steam', 'compatdata', 'off-before', 'on', 'off-after']:
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
            cleanup_env = None
            error = None
            try:
                for phase in ['off-before', 'on', 'off-after']:
                    if phase == 'on':
                        # Simulate a pre-A2 prefix from the old published tool.
                        # This is the sole deliberate prefix mutation; the gate
                        # never repairs/replaces the resulting stubs itself.
                        legacy = prefix / 'drive_c/windows' / ('syswow64' if winearch == 'wow64' else 'system32') / 'nvcuda.dll'
                        legacy.unlink()
                        legacy.write_bytes(b'\x7fELFlegacy-managed-nvcuda-fixture')
                        (directory / 'compatdata/zluda-cuda-roots-v1.json').unlink()
                        (prefix / '.update-timestamp').write_text('0')
                        # Avoid downloads/installers in the actual Asylum fix.
                        (prefix / 'winetricks.log').write_text('d3dx9\nd3dcompiler_43\nphysx\n')
                    env = environment(disposable, directory, winearch, phase)
                    cleanup_env = wine_cpu.environment(disposable, directory, disposable / 'files/bin/wine',
                                                       disposable / 'files/bin/wineserver', winearch)
                    cleanup_env['WINEPREFIX'] = str(prefix)
                    verb = 'waitforexitandrun' if winearch == 'wow64' and phase == 'on' else 'run'
                    # A Windows path takes Proton's normal non-Steam Wine entry,
                    # avoiding umu.exe / Steam itself, with the shipping script.
                    target = 'Z:' + str(fixtures / profile['probe']).replace('/', '\\')
                    argv = [disposable / 'proton', verb, target]
                    if phase != 'on':
                        argv.append('--expect-stub')
                    code = wine_cpu.command(argv, env, directory / phase / 'launcher.log', 240)
                    result = admit_case(directory / phase, phase, winearch, disposable, runner, code)
                    result.update(phase=phase, command=[str(arg) for arg in argv],
                                  prefix_stubs=stub_hashes(disposable, prefix),
                                  template_stubs=stub_hashes(disposable, default))
                    report['cases'].append(result)
                    if phase == 'on':
                        migration = json.loads((directory / 'compatdata/zluda-cuda-roots-v1.json').read_text())
                        expected = 'drive_c/windows/' + ('syswow64' if winearch == 'wow64' else 'system32') + '/nvcuda.dll'
                        if expected not in migration['replaced']:
                            raise ValueError('real launcher did not migrate seeded legacy nvcuda')
                        report['migration'] = migration
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
        if set(record['profiles']) != set(wine_cpu.PROFILES) or any(len(p['cases']) != 3 for p in record['profiles'].values()):
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
