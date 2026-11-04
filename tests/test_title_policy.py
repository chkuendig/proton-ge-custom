"""Title activation order, installer isolation and B5 product behavior."""
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proton_zluda as policy


class TitlePolicy(unittest.TestCase):
    def test_five_title_defaults_explicit_zero_and_helper_verbs(self):
        for appid in policy.TITLE_DEPENDENCIES:
            for requested in [None, '0', '1']:
                env = {'SteamAppId': appid}
                if requested is not None:
                    env['PROTON_ENABLE_ZLUDA'] = requested
                policy.early_title_policy(env, 'run')
                self.assertEqual(env['PROTON_ENABLE_ZLUDA'], requested or '1')
                self.assertEqual(env.get('PROTON_USE_WOW64'),
                                 '1' if appid in policy.PE32_TITLES and requested != '0' else None)
                self.assertEqual(env.get('PROTON_FORCE_NVAPI'), '1' if requested != '0' else None)
            env = {'SteamAppId': appid}
            policy.early_title_policy(env, 'runinprefix')
            self.assertNotIn('PROTON_ENABLE_ZLUDA', env)
        env = {'SteamAppId': '999999'}
        policy.early_title_policy(env, 'run')
        self.assertNotIn('PROTON_ENABLE_ZLUDA', env)

    def setup_tool(self, root):
        tool = root / 'tool'
        hashes = {}
        for arch in policy.ARCHES.values():
            path = tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.write_bytes(arch.encode())
            hashes[arch] = hashlib.sha256(path.read_bytes()).hexdigest()
        (tool / 'files/lib/wine-cuda/stubs.json').write_text(json.dumps(
            {'proton_pin': policy.VALVE_PROTON_PIN, 'sha256': hashes}))
        (tool / 'provenance').mkdir()
        (tool / 'provenance/VALVE-STOCK.json').write_text(json.dumps(
            {'proton_pin': policy.VALVE_PROTON_PIN, 'nvcuda_sha256': hashes}))
        prefix = root / 'prefix'
        prefix.mkdir()
        proton = SimpleNamespace(base_dir=tool, wine_bin=str(tool / 'files/bin/wine'),
                                 wineserver_bin=str(tool / 'files/bin/wineserver'))
        data = SimpleNamespace(prefix_dir=str(prefix), prefix_lock=nullcontext())
        return tool, prefix, proton, data

    def test_prerequisites_run_with_stock_stubs_and_missing_install_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool, prefix, proton, data = self.setup_tool(Path(tmp))
            env = {'SteamAppId': '49520', 'PROTON_ENABLE_ZLUDA': '1',
                   'ZLUDA_WINE_BACKEND_PATH': '/wrong/backend',
                   'LD_LIBRARY_PATH': str(tool / 'files/lib64')}
            session = SimpleNamespace(env=env)
            argv = ['proton', 'run', 'Z:/game/Launcher.exe']
            with patch.object(policy.subprocess, 'run') as run:
                policy.late_title_policy(session, proton, data, argv, 'LD_LIBRARY_PATH', lambda *a: None)
            installer = run.call_args_list[1]
            self.assertEqual(installer.args[0][-2:], ['--unattended', 'physx'])
            actual = installer.kwargs['env']
            self.assertIn('/wine-cuda/stub', actual['WINEDLLPATH'])
            self.assertNotIn('ZLUDA_WINE_BACKEND_PATH', actual)
            self.assertEqual(actual['LD_LIBRARY_PATH'], '')
            self.assertEqual(argv, ['proton', 'waitforexitandrun', 'Z:/game/Borderlands2.exe', '-NoSplash'])
            import subprocess
            with patch.object(policy.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'installer')):
                with self.assertRaises(subprocess.CalledProcessError):
                    policy.late_title_policy(session, proton, data, argv, 'LD_LIBRARY_PATH', lambda *a: None)

    def test_mirror_moves_local_files_only_after_prerequisites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool, prefix, proton, data = self.setup_tool(root)
            binaries = root / 'game/Binaries'
            binaries.mkdir(parents=True)
            for name in ['PhysXCore.dll', 'PhysXDevice.dll']:
                (binaries / name).write_text('game-local fixture')
            session = SimpleNamespace(env={'SteamAppId': '17410', 'STEAM_COMPAT_INSTALL_PATH': str(root / 'game')})
            argv = ['proton', 'run', 'Z:/game/Binaries/MirrorsEdge.exe']
            import subprocess
            with patch.object(policy.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'installer')):
                with self.assertRaises(subprocess.CalledProcessError):
                    policy.late_title_policy(session, proton, data, argv, 'LD_LIBRARY_PATH', lambda *a: None)
            self.assertTrue((binaries / 'PhysXCore.dll').exists())
            (prefix / 'winetricks.log').write_text('d3dx9\nd3dcompiler_43\nphysx\n')
            with patch.object(policy.subprocess, 'run') as run:
                policy.late_title_policy(session, proton, data, argv, 'LD_LIBRARY_PATH', lambda *a: None)
                run.assert_not_called()
            self.assertEqual((binaries / 'PhysXCore.dll.bak').read_text(), 'game-local fixture')
            self.assertFalse((binaries / 'PhysXCore.dll').exists())
