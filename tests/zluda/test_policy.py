#!/usr/bin/env python3
import os
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import proton_zluda as policy


class Policy(unittest.TestCase):
    def test_same_build_stubs_and_private_prefix_copies(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool = Path(temporary)
            cuda = tool / 'files/lib/wine-cuda'
            hashes = {}
            for arch in policy.ARCHES.values():
                path = cuda / 'stub' / arch / 'nvcuda.dll'
                path.parent.mkdir(parents=True)
                path.write_bytes(('own Wine output: ' + arch).encode())
                hashes[arch] = hashlib.sha256(path.read_bytes()).hexdigest()
            record = dict(proton_pin=policy.VALVE_PROTON_PIN, wine_pin=policy.VALVE_WINE_PIN,
                          source_sha256=policy.VALVE_STUB_SOURCES, sha256=hashes)
            manifest = cuda / 'stubs.json'
            manifest.write_text(json.dumps(record))
            policy.stub_assets(tool)
            prefix = tool / 'prefix'
            policy.materialize_default_prefix(tool, prefix)
            for directory, arch in policy.ARCHES.items():
                copy = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
                original = cuda / 'stub' / arch / 'nvcuda.dll'
                self.assertNotEqual(copy.stat().st_ino, original.stat().st_ino)
                copy.write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    policy.validate_default_prefix(tool, prefix)
                policy.materialize_default_prefix(tool, prefix)
                copy.unlink()
                copy.symlink_to(original)
                with self.assertRaises(ValueError):
                    policy.validate_default_prefix(tool, prefix)
                policy.materialize_default_prefix(tool, prefix)
            for field in ('proton_pin', 'wine_pin', 'source_sha256', 'sha256'):
                manifest.write_text(json.dumps(dict(record, **{field: {}})))
                with self.assertRaises(ValueError):
                    policy.stub_assets(tool)
            manifest.write_text(json.dumps(record))
            original.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                policy.stub_assets(tool)

    def test_settings_display_detection_and_explicit_opt_out(self):
        hardware = [([(0x030000, 0x1002)], '1'), ([], '0'),
                    ([(0x030000, 0x1002), (0x030200, 0x10de)], '0'),
                    ([(0x030000, 0x1002), (0x040300, 0x10de)], '1')]
        with tempfile.TemporaryDirectory() as temporary:
            for i, (devices, default) in enumerate(hardware):
                directory = Path(temporary) / str(i)
                directory.mkdir()
                for j, (cls, vendor) in enumerate(devices):
                    device = directory / str(j)
                    device.mkdir()
                    (device / 'class').write_text(hex(cls))
                    (device / 'vendor').write_text(hex(vendor))
                for app in policy.TITLE_DEPENDENCIES:
                    for explicit in (None, '0', '1'):
                        for setting in (None, '0', 1):
                            env = {'SteamAppId': app}
                            if explicit is not None:
                                env['PROTON_ENABLE_ZLUDA'] = explicit
                            settings = {} if setting is None else {'PROTON_ENABLE_ZLUDA': setting}
                            expected = explicit if explicit is not None else str(setting) if setting is not None else default
                            with mock.patch.dict(os.environ, env, clear=True), \
                                 mock.patch.dict(sys.modules, {'user_settings': SimpleNamespace(user_settings=settings)}), \
                                 mock.patch.object(policy, 'Path', return_value=directory):
                                policy.early_title_policy(os.environ, 'run')
                                self.assertEqual(os.environ['PROTON_ENABLE_ZLUDA'], expected)
                                self.assertEqual(os.environ.get('PROTON_USE_WOW64'), '1' if expected == '1' and app in policy.PE32_TITLES else None)
        env = {'SteamAppId': '49520', 'PROTON_ENABLE_ZLUDA': '1'}
        policy.early_title_policy(env, 'runinprefix')
        self.assertNotIn('PROTON_USE_WOW64', env)

    def test_deck_detection_fails_closed_and_preserves_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dmi, nodes = root / 'dmi', root / 'nodes'
            dmi.mkdir(); nodes.mkdir()
            for name, value in [('sys_vendor', 'Valve'), ('product_name', 'Jupiter'), ('board_name', 'Galileo')]:
                (dmi / name).write_text(value)
            for values in ([0, 100303], [100303, 100303], [100302], ['bad'], []):
                for node in nodes.iterdir():
                    shutil.rmtree(node)
                for i, value in enumerate(values):
                    node = nodes / str(i)
                    node.mkdir()
                    (node / 'properties').write_text('gfx_target_version ' + str(value))
                for on in ('0', '1'):
                    env = {'PROTON_ENABLE_ZLUDA': on}
                    with mock.patch.object(policy, 'Path', side_effect=lambda name: dmi if name == '/sys/class/dmi/id' else nodes):
                        policy.configure_device(env)
                    self.assertEqual(env.get('HSA_OVERRIDE_GFX_VERSION'), '10.3.0' if on == '1' and values == [0, 100303] else None)
            env = {'PROTON_ENABLE_ZLUDA': '1', 'HSA_OVERRIDE_GFX_VERSION': 'user'}
            policy.configure_device(env)
            self.assertEqual(env['HSA_OVERRIDE_GFX_VERSION'], 'user')


if __name__ == '__main__':
    unittest.main()
