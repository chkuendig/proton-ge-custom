"""Real-launcher command construction/admission; stand-ins execute no Wine."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PROTON = Path(__import__('os').environ.get('PROTON_SOURCE_ROOT', ROOT / 'proton-source')).resolve()
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import launcher_cpu as gate
import proton_zluda


class LauncherCpu(unittest.TestCase):
    def test_valve_helper_requires_exact_stock_disabled_result_and_no_override(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(gate.PROFILE, base='valve'):
            root = Path(temporary)
            tool, prefix = root / 'tool', root / 'prefix'
            prefix.mkdir()
            registry = prefix / 'user.reg'
            registry.write_text('WINE REGISTRY Version 2\n\n[Software\\\\Wine\\\\DllOverrides] 1\n"nvcuda"="disabled"\n')
            env = {'WINEDLLPATH': str(tool / 'files/lib/wine-cuda/stub')}
            capture = root / 'launch-env.json'
            capture.write_text(json.dumps(env))
            (root / 'launcher.log').write_text('')
            for directory, arch in proton_zluda.ARCHES.items():
                for path in [tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll',
                             prefix / 'drive_c/windows' / directory / 'nvcuda.dll']:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'stock stub')
            runner = SimpleNamespace()
            for arch, appid, failure in [('win64', '999999', 'fixture failure at line 75: module!=NULL'),
                                         ('wow64', '35140', 'PE32 fixture failure at line 65: module')]:
                output = root / ('steam-' + appid + '.log')
                output.write_text(failure + ' (last error 126)\n')
                result = gate.admit_case(root, 'helper', arch, tool, runner, 1)
                self.assertEqual(result['load_library']['last_error'], 126)
                with self.assertRaisesRegex(ValueError, 'exactly error 126'):
                    gate.admit_case(root, 'helper', arch, tool, runner, 0)
                output.write_text(failure + ' (last error 127)\n')
                with self.assertRaisesRegex(ValueError, 'exactly error 126'):
                    gate.admit_case(root, 'helper', arch, tool, runner, 1)
                with mock.patch.dict(gate.PROFILE, base='ge'), self.assertRaisesRegex(ValueError, 'probe failed'):
                    gate.admit_case(root, 'helper', arch, tool, runner, 1)
            self.assertEqual(gate.admit_policy(root, 'helper', tool, prefix, [])['registry_nvcuda'], 'disabled')
            capture.write_text(json.dumps(dict(env, WINEDLLOVERRIDES='nvcuda=b')))
            with self.assertRaisesRegex(ValueError, 'stock registry load order'):
                gate.admit_policy(root, 'helper', tool, prefix, [])
            capture.write_text(json.dumps(env))
            registry.write_text(registry.read_text().replace('"disabled"', '"builtin"'))
            with self.assertRaisesRegex(ValueError, 'registry must disable'):
                gate.admit_policy(root, 'helper', tool, prefix, [])

    def test_valve_replay_retains_captured_environment_cwd_and_target_arguments(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(gate.PROFILE, base='valve'):
            root = Path(temporary)
            tool = root / 'tool'
            for name in ['files/bin/wine', 'files/lib/wine/x86_64-unix/wine-preloader']:
                path = tool / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('#!/usr/bin/python3\nimport json,os,sys\nopen(os.environ["FORWARDED"],"w").write(json.dumps({"argv":sys.argv[1:],"env":dict(os.environ),"cwd":os.getcwd()}))\n')
                path.chmod(0o755)
            gate.install_capture(tool)
            preloader = str(tool) + '/files/lib//wine/x86_64-unix/wine-preloader'
            arguments = [str(tool) + '/files/lib//wine/x86_64-unix/wine', r'c:\windows\system32\steam.exe', r'Z:\game\probe.exe', '--expect-stub']
            env = dict(os.environ, PROTON_LOG_DIR=str(root), PROTON_ENABLE_ZLUDA='0',
                       WINELOADERNOEXEC='1', FORWARDED=str(root / 'forwarded.json'))
            cwd = root / 'cwd'
            cwd.mkdir()
            subprocess.run([preloader, *arguments], env=env, cwd=cwd, check=True)
            self.assertEqual(json.loads((root / 'launch-command.json').read_text()), [preloader, *arguments])
            forwarded = json.loads((root / 'forwarded.json').read_text())
            self.assertEqual(forwarded['argv'], [arguments[0], *arguments[2:]])
            self.assertEqual(forwarded['env'], json.loads((root / 'launch-env.json').read_text()))
            receipt = gate.admit_capture(root, tool, arguments[2:])
            self.assertEqual(receipt['cwd'], forwarded['cwd'])
            self.assertEqual(receipt['cwd'], str(cwd))
            self.assertEqual(receipt['steam_client_handshake'], 'pending Deck qualification')
            self.assertEqual(json.loads((root / 'launch-env.json').read_text())['PROTON_ENABLE_ZLUDA'], '0')
            with self.assertRaisesRegex(ValueError, 'stock preloader/Steam dispatch'):
                gate.admit_capture(root, tool, [r'Z:\wrong.exe'])
            original = (root / 'launch-replay.json').read_bytes()
            receipt['replay_argv'][-1] = '--wrong-argument'
            (root / 'launch-replay.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'replay differs'):
                gate.admit_capture(root, tool, arguments[2:])
            (root / 'launch-replay.json').write_bytes(original)
            captured_env = (root / 'launch-env.json').read_bytes()
            (root / 'launch-env.json').write_bytes(captured_env + b'\n')
            with self.assertRaisesRegex(ValueError, 'replay differs'):
                gate.admit_capture(root, tool, arguments[2:])
            (root / 'launch-env.json').write_bytes(captured_env)
            (root / 'launch-command.json').write_text(json.dumps(['bypassed-steam.exe']))
            with self.assertRaisesRegex(ValueError, 'stock preloader/Steam dispatch'):
                gate.admit_policy(root, 'off-before', tool, root / 'prefix', arguments[2:])
            subprocess.run([str(tool / 'files/bin/wine'), *arguments[2:]], env=env, cwd=cwd, check=True)
            self.assertEqual(json.loads((root / 'forwarded.json').read_text())['argv'], arguments[2:])
            with mock.patch.dict(os.environ, {'DISPLAY': ':77'}, clear=True):
                self.assertNotIn('DISPLAY', gate.environment(tool, root, 'win64', 'off-before'))
            with mock.patch.dict(os.environ, {'DISPLAY': ':77', 'CI_HEADLESS_DISPLAY': '1'}, clear=True):
                self.assertEqual(gate.environment(tool, root, 'win64', 'off-before')['DISPLAY'], ':77')

    def test_environment_lets_proton_own_namespace_and_real_protonfix_own_wow64(self):
        with mock.patch.dict(os.environ, {'WINEDLLPATH': '/leaked', 'WINEDLLOVERRIDES': 'nvcuda=n',
                              'WINEARCH': 'win32', 'PROTON_FORCE_NVAPI': '1',
                              'ZLUDA_WINE_BACKEND_PATH': '/leaked', 'SteamAppId': 'wrong'}, clear=True):
            for arch in ['win64', 'wow64']:
                for phase in gate.PHASES:
                    env = gate.environment(Path('/tool'), Path('/work/profile'), arch, phase)
                    for key in ['WINEDLLPATH', 'WINEDLLOVERRIDES', 'WINEPREFIX', 'WINEARCH',
                                'PROTON_FORCE_NVAPI', 'ZLUDA_WINE_BACKEND_PATH']:
                        self.assertNotIn(key, env)
                    if arch == 'wow64' and phase.startswith('on'):
                        self.assertNotIn('PROTON_ENABLE_ZLUDA', env)
                        self.assertNotIn('PROTON_USE_WOW64', env)
                        self.assertEqual(env['UMU_ID'], '35140')
                    else:
                        self.assertEqual(env['PROTON_ENABLE_ZLUDA'], '1' if phase.startswith('on') else '0')

    def exercise(self, failure=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, tool, fixtures, work = [root / name for name in ['src', 'tool', 'fixtures', 'work']]
            fixtures.mkdir(); tool.mkdir(); source.mkdir()
            for profile in gate.wine_cpu.PROFILES.values():
                (fixtures / profile['backend']).write_bytes(b'fake ' + profile['backend'].encode())
                (fixtures / profile['probe']).write_bytes(b'probe')
            for bits in ['64', '32']:
                (fixtures / ('nvcuda-decoy' + bits + '.dll')).write_bytes(b'native decoy')
            legacy = tool / 'files/lib/wine/nvidia-libs/nvcuda/x86_64-windows/nvcuda.dll.so'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'Winelib ELF')
            inf = tool / 'files/share/wine/wine.inf'
            inf.parent.mkdir(parents=True)
            inf.write_text('Wine version')
            (tool / 'files/lib64').mkdir(parents=True)
            (tool / 'files/lib64/libcuda.so.1').write_bytes(b'production backend')
            (tool / 'files/share/default_pfx').mkdir(parents=True)
            calls, cleaned = [], []
            hashes = {}
            for arch in proton_zluda.ARCHES.values():
                path = tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll'
                path.parent.mkdir(parents=True); path.write_bytes(arch.encode())
                hashes[arch] = gate.sha256(path)
                active = tool / 'files/lib/wine-cuda/zluda' / arch / 'nvcuda.dll'
                active.parent.mkdir(parents=True)
                active.write_bytes(arch.encode() if failure == 'seed-noop' else b'active builtin')
            def command(argv, env, log, timeout, accepted=(0,)):
                log.parent.mkdir(parents=True, exist_ok=True); log.write_text('launcher fixture')
                if argv[0] == 'cp':
                    shutil.copytree(tool, work / 'tool', symlinks=True)
                    return 0
                calls.append((argv, dict(env)))
                arch = 'wow64' if env['UMU_ID'] == '35140' else 'win64'
                phase = Path(env['PROTON_LOG_DIR']).name
                prefix = Path(env['STEAM_COMPAT_DATA_PATH']) / 'pfx'
                for base in [prefix, work / 'tool/files/share/default_pfx']:
                    for directory, pe_arch in proton_zluda.ARCHES.items():
                        path = base / 'drive_c/windows' / directory / 'nvcuda.dll'
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(pe_arch.encode())
                timestamp = prefix / '.update-timestamp'
                if not timestamp.exists():
                    timestamp.write_text(str(int(inf.stat().st_mtime)))
                if failure == 'timeout' and phase.startswith('on'):
                    raise TimeoutError('probe timeout')
                if failure == 'prefix-stub' and phase.startswith('on'):
                    (prefix / 'drive_c/windows/system32/nvcuda.dll').write_bytes(b'wrong')
                # Admission itself has separate negatives below; the orchestration
                # stand-in focuses on real launcher argv/prefix/cleanup sequencing.
                return 0
            runner = SimpleNamespace(REQUIRED_EVENTS={'event': 1}, WOW64_EVENTS={'event32': 1})
            with mock.patch.object(gate.wine_cpu, 'admit_cpu_devices'), \
                 mock.patch.object(gate.wine_cpu, 'admit_source', return_value=source), \
                 mock.patch.object(gate.wine_cpu, 'admit_tool'), \
                 mock.patch.object(gate, 'fixture_runner', return_value=runner), \
                 mock.patch.object(gate.wine_cpu, 'command', side_effect=command), \
                 mock.patch.object(gate, 'install_capture'), \
                 mock.patch.object(gate, 'admit_policy', side_effect=ValueError('policy mismatch') if failure == 'policy' else lambda *a: {}), \
                 mock.patch.object(gate, 'admit_case', side_effect=lambda *a: {'status': 'passed'}), \
                 mock.patch.object(gate.wine_cpu, 'cleanup_server', side_effect=lambda s,e,w: cleaned.append(e['WINEPREFIX']) or {'status':'passed'}), \
                 mock.patch.object(proton_zluda, 'STUB_SHA256', hashes):
                if failure:
                    with self.assertRaises((ValueError, TimeoutError)):
                        gate.run(source, tool, fixtures, work)
                else:
                    record = gate.run(source, tool, fixtures, work)
                    self.assertEqual(record['status'], 'passed')
                    self.assertEqual([argv[1] for argv, env in calls],
                        ['waitforexitandrun' if arch == 'wow64' and phase.startswith('on') else 'run'
                         for arch in ['win64', 'wow64'] for phase in gate.PHASES])
                    self.assertEqual([p['phase'] for p in record['profiles']['wow64']['cases']],
                                     gate.PHASES)
                    for profile in record['profiles'].values():
                        for case in profile['cases']:
                            if case['phase'] in ('off-a1', 'on-a1'):
                                self.assertEqual(case['upgrade_fixture'], 'A1-layout (stand-in modules)')
            self.assertEqual((tool / 'files/lib64/libcuda.so.1').read_bytes(), b'production backend')
            self.assertGreaterEqual(len(cleaned), 1)
            self.assertEqual(json.loads((work / 'INTEGRATION.json').read_text())['status'],
                             'failed' if failure else 'passed')
            for argv, env in calls:
                self.assertEqual(Path(argv[0]).name, 'proton')
                self.assertNotIn('WINEDLLPATH', env)
                self.assertTrue(argv[2].startswith('Z:'))

    def test_dual_prefix_off_on_off_real_launcher_composition(self):
        self.exercise()

    def test_timeout_migration_and_prefix_mismatch_fail_and_cleanup(self):
        for mode in ['timeout', 'policy', 'seed-noop']:
            with self.subTest(mode=mode):
                self.exercise(mode)

    def test_probe_success_cannot_hide_wrong_events_or_protonfix_skip(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            logfile = directory / 'steam-35140.log'
            logfile.write_text('PASS: fixture')
            runner = SimpleNamespace(WOW64_EVENTS={'event':1}, wow64_receipt=lambda *a: {}, wow64_witness=lambda *a: {})
            with self.assertRaisesRegex(ValueError, 'events differ'):
                gate.admit_case(directory, 'on', 'wow64', Path('/tool'), runner, 0)
            (directory / 'backend.log').write_text('event\n')
            (directory / 'launcher.log').write_text('early was skipped')
            with self.assertRaisesRegex(ValueError, 'protonfix did not run'):
                gate.admit_case(directory, 'on', 'wow64', Path('/tool'), runner, 0)
            logfile.write_text('not a probe pass')
            with self.assertRaisesRegex(ValueError, 'probe failed'):
                gate.admit_case(directory, 'off-after', 'wow64', Path('/tool'), runner, 0)

    def test_integrity_gates_survive_python_optimization_and_caches_lock_all_inputs(self):
        for name in ['inputs.py', 'overlay.py']:
            tree = ast.parse((ROOT / 'packaging/zluda-overlay' / name).read_text())
            self.assertFalse(any(isinstance(n, ast.Assert) for n in ast.walk(tree)))
        workflow = (ROOT / '.github/workflows/zluda-overlay.yml').read_text()
        self.assertNotIn('/work/cargo/git', workflow)
        self.assertNotIn('/work/cargo/registry/src', workflow)
        for line in workflow.splitlines():
            if 'key: llvm-v3-' in line or 'key: cargo-archives-v2-' in line:
                for name in ['inputs.lock.json', 'zluda/Cargo.lock', 'zluda-overlay/*.sh', 'zluda-overlay/*.py', 'zluda-overlay.yml']:
                    self.assertIn(name, line)
