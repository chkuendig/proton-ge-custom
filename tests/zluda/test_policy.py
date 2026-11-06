#!/usr/bin/env python3
import os
import importlib.machinery
import importlib.util
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
loader = importlib.machinery.SourceFileLoader('proton', str(Path(__file__).resolve().parents[2] / 'proton'))
policy = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(policy)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'make'))
import zluda as checks


class Policy(unittest.TestCase):
    def test_prefix_copies_detach_links_without_changing_the_supplier(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tool, supplier = root / 'tool', root / 'supplier/lib/wine/x86_64-windows'
            supplier.mkdir(parents=True)
            compat = policy.CompatData(str(root / 'compatdata'))
            pairs = []
            for directory in policy.ARCHES:
                windows = Path(compat.prefix_dir) / 'drive_c/windows' / directory
                windows.mkdir(parents=True)
                for name in ('builtin.dll', 'icuin68.dll'):
                    donor = supplier / (directory + name)
                    donor.write_bytes(bytes(64) + b'Wine builtin DLL' if name == 'builtin.dll' else b'ICU fixture')
                    for kind in ('symlink', 'hardlink'):
                        path = windows / kind / name
                        path.parent.mkdir(exist_ok=True)
                        path.symlink_to(donor) if kind == 'symlink' else os.link(donor, path)
                        pairs.append((path, donor, donor.read_bytes()))
                template = windows / 'template'
                template.symlink_to(donor)
                with mock.patch.dict(os.environ, PROTON_DLL_COPY='*'):
                    compat.pfx_copy(str(template), str(windows / 'initial.dll'))
                template.unlink()
                self.assertFalse((windows / 'initial.dll').is_symlink())
            compat.isolate_module_links(tool)
            for path, donor, original in pairs:
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.stat().st_nlink, 1)
                self.assertEqual(path.read_bytes(), original)
                path.write_bytes(b'Wine prefix update')
                self.assertEqual(donor.read_bytes(), original)
            failed = windows / 'failed.dll'
            failed.symlink_to(pairs[0][1])
            with mock.patch.object(policy.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
                with self.assertRaises(OSError):
                    compat.isolate_module_links(tool)
            self.assertEqual(pairs[0][1].read_bytes(), pairs[0][2])

    def test_module_directory_links_into_tools_refuse_isolation(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(policy, 'log'):
            root = Path(temporary)
            tool = root / 'tool'
            for directory in policy.ARCHES:
                for target in (tool, root / 'other-tool'):
                    target.mkdir(exist_ok=True)
                    (target / 'toolmanifest.vdf').touch()
                    donor = target / 'builtin.dll'
                    donor.write_bytes(b'unchanged')
                    before = (donor.read_bytes(), donor.stat())
                    compat = policy.CompatData(str(root / (directory + target.name)))
                    windows = Path(compat.prefix_dir) / 'drive_c/windows'
                    windows.mkdir(parents=True)
                    (windows / directory).symlink_to(target)
                    self.assertIs(compat.isolate_module_links(tool), False)
                    self.assertEqual((donor.read_bytes(), donor.stat()), before)

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
            record = dict(proton_pin=checks.VALVE_PROTON_PIN, wine_pin=checks.VALVE_WINE_PIN,
                          source_sha256=checks.VALVE_STUB_SOURCES, sha256=hashes)
            manifest = cuda / 'stubs.json'
            manifest.write_text(json.dumps(record))
            checks.stub_assets(tool)
            prefix = tool / 'prefix'
            policy.materialize_default_prefix(tool, prefix)
            before = [(path.stat().st_ino, path.stat().st_mtime_ns)
                      for path in prefix.rglob('nvcuda.dll')]
            policy.materialize_default_prefix(tool, prefix)
            self.assertEqual([(path.stat().st_ino, path.stat().st_mtime_ns)
                              for path in prefix.rglob('nvcuda.dll')], before)
            for directory, arch in policy.ARCHES.items():
                copy = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
                original = cuda / 'stub' / arch / 'nvcuda.dll'
                self.assertNotEqual(copy.stat().st_ino, original.stat().st_ino)
                copy.write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    checks.validate_default_prefix(tool, prefix)
                policy.materialize_default_prefix(tool, prefix)
                copy.unlink()
                copy.symlink_to(original)
                with self.assertRaises(ValueError):
                    checks.validate_default_prefix(tool, prefix)
                policy.materialize_default_prefix(tool, prefix)
            for field in ('proton_pin', 'wine_pin', 'source_sha256', 'sha256'):
                manifest.write_text(json.dumps(dict(record, **{field: {}})))
                with self.assertRaises(ValueError):
                    checks.stub_assets(tool)
            manifest.write_text(json.dumps(record))
            original.write_bytes(b'tampered')
            with self.assertRaises(ValueError):
                checks.stub_assets(tool)

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
