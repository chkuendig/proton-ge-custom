"""Game defaults use real applied fixes; relocated prefixes keep private builtins."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import proton_zluda
import test_prefix_isolation as isolation

PROTON = Path(os.environ['PROTON_SOURCE_ROOT'])
FIXES = Path(os.environ.get('PROTONFIXES_SOURCE_ROOT', PROTON / 'protonfixes'))
APPS = ('17410', '35140', '50130', '208650', '49520')


class LauncherBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pin = subprocess.check_output(['git', '-C', str(PROTON), 'rev-parse', 'HEAD:protonfixes'], text=True).strip()
        cls.games = {}
        with tempfile.TemporaryDirectory() as temporary:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / 'index'))
            git = ['git', '-C', str(FIXES)]
            subprocess.run(git + ['read-tree', pin], env=env, check=True)
            for patch in sorted((PROTON / 'patches/protonfixes').glob('*.patch')):
                subprocess.run(git + ['apply', '--cached', '--whitespace=nowarn', str(patch)], env=env, check=True)
            for app in APPS:
                cls.games[app] = subprocess.check_output(git + ['show', ':gamefixes-steam/' + app + '.py'], env=env, text=True)

    def test_real_five_title_defaults_and_explicit_precedence(self):
        hardware = {'AMD': ([(0x030000, 0x1002)], '1'),
                    'NVIDIA': ([(0x030200, 0x10de)], '0'),
                    'Intel': ([(0x038000, 0x8086)], '0'), 'none': ([], '0'),
                    'hybrid': ([(0x030000, 0x1002), (0x030200, 0x10de)], '0'),
                    'AMD-Intel-audio': ([(0x030200, 0x1002), (0x030000, 0x8086), (0x040300, 0x10de)], '1'),
                    'malformed': ([(0x030000, 0x1002), ('invalid', 0x10de)], '0'),
                    'missing': ([], '0')}
        with tempfile.TemporaryDirectory() as temporary:
            for name, (devices, default) in hardware.items():
                directory = Path(temporary) / name
                if name != 'missing':
                    directory.mkdir()
                for i, (device_class, vendor) in enumerate(devices):
                    device = directory / str(i)
                    device.mkdir()
                    (device / 'class').write_text(hex(device_class) if isinstance(device_class, int) else device_class)
                    (device / 'vendor').write_text(hex(vendor))
                for app, code in self.games.items():
                    for explicit in (None, '0', '1'):
                        for setting in (None, '0', '1', 0, 1):
                            with self.subTest(app=app, hardware=name, env=explicit, user_setting=setting):
                                env = {} if explicit is None else {'PROTON_ENABLE_ZLUDA': explicit}
                                settings = {} if setting is None else {'PROTON_ENABLE_ZLUDA': setting}
                                def set_environment(key, value):
                                    os.environ[key] = value
                                modules = {'protonfixes': SimpleNamespace(util=SimpleNamespace(set_environment=set_environment)),
                                           'protonfixes.logger': SimpleNamespace(log=None),
                                           'utilities': None, 'vulkan': None,
                                           'user_settings': SimpleNamespace(user_settings=settings)}
                                expected = explicit if explicit is not None else str(setting) if setting is not None else default
                                with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(sys.modules, modules), \
                                     mock.patch.object(proton_zluda, 'Path', return_value=directory) as sysfs:
                                    game = {}
                                    exec(compile(code, 'gamefixes-steam/' + app + '.py', 'exec'), game)
                                    game['early']()
                                    self.assertEqual(os.environ['PROTON_ENABLE_ZLUDA'], expected)
                                    self.assertEqual(os.environ.get('PROTON_USE_WOW64'),
                                                     '1' if expected == '1' and app != '208650' else None)
                                self.assertEqual(sysfs.called, explicit is None and setting is None)

    def test_missing_or_invalid_user_settings_preserves_hardware_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            device = directory / 'display'
            device.mkdir()
            (device / 'class').write_text('0x030000')
            (device / 'vendor').write_text('0x1002')
            for settings in (None, SimpleNamespace(user_settings=None)):
                modules = {'user_settings': settings, 'utilities': None, 'vulkan': None}
                with mock.patch.dict(os.environ, {}, clear=True), mock.patch.dict(sys.modules, modules), \
                     mock.patch.object(proton_zluda, 'Path', return_value=directory):
                    self.assertEqual(proton_zluda.game_setting(), '1')

    def test_relocated_prefix_roots_detach_foreign_builtin_links(self):
        source = (PROTON / 'proton').read_text()
        ns = isolation.functions(source)
        for component in ('pfx', 'drive_c', 'windows'):
            for kind in ('symlink', 'hardlink'):
                with self.subTest(component=component, kind=kind), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    foreign = root / 'other-tool/control.dll'
                    foreign.parent.mkdir()
                    foreign.write_bytes(isolation.builtin('foreign'))
                    prefix = root / 'compatdata/pfx'
                    windows = prefix / 'drive_c/windows'
                    (windows / 'system32').mkdir(parents=True)
                    location = {'pfx': prefix, 'drive_c': windows.parent, 'windows': windows}[component]
                    relocated = root / 'relocated'
                    shutil.move(location, relocated)
                    location.symlink_to(relocated, target_is_directory=True)
                    leaf = windows / 'system32/control.dll'
                    if kind == 'symlink':
                        leaf.symlink_to(foreign)
                    else:
                        os.link(foreign, leaf)
                    before = foreign.read_bytes()
                    ns['isolate_module_links'](SimpleNamespace(prefix_dir=str(prefix)), root / 'our-tool')
                    self.assertTrue(location.is_symlink())
                    self.assertFalse(leaf.is_symlink())
                    self.assertNotEqual(leaf.stat().st_ino, foreign.stat().st_ino)
                    isolation.inplace(leaf, isolation.builtin('prefix update'))
                    self.assertEqual(foreign.read_bytes(), before)
