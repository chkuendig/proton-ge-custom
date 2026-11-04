"""Real-launcher command construction/admission; stand-ins execute no Wine."""
import ast
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import launcher_cpu as gate
import proton_zluda


class LauncherCpu(unittest.TestCase):
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
