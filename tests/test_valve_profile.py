"""Valve profile admission, title/hardware selection and private prefix policy."""
import ast
from contextlib import nullcontext
import hashlib
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging/zluda-overlay"))
import proton_zluda as policy
from inputs import PROFILE, LOCK, PROTON_ROOT, check_profile_source, native_control
import test_prefix_isolation as isolation
import test_cuda_roots as roots
from test_prefix_isolation import functions, builtin, inplace
PROTON = PROTON_ROOT


@unittest.skipUnless(PROFILE['base'] == 'valve', 'Valve profile')
class ValveIsolation(isolation.Isolation):
    @classmethod
    def setUpClass(cls):
        cls.source = (PROTON / 'proton').read_text()
        cls.old_source = subprocess.check_output(['git', '-C', str(PROTON), 'show', LOCK['proton_base'] + ':proton'], text=True)

    def setUp(self):
        super().setUp()
        tool = self.root / 'compatibilitytools.d/ours'
        tool.parent.mkdir()
        self.tool.rename(tool)
        self.own = tool / self.own.relative_to(self.tool)
        self.tool = tool

    test_linked_parent_requires_refusal_not_leaf_copy = None
    def test_linked_nested_parent_is_logged_and_skipped(self):
        self.dst.parent.rmdir()
        self.dst.parent.symlink_to(self.other.parent)
        inplace(self.dst, builtin('unfixed parent write'))
        self.assertEqual(self.other.read_bytes(), builtin('unfixed parent write'))
        before = self.other.read_bytes()
        self.ns['log'] = mock.Mock()
        self.isolate()
        self.assertTrue(self.dst.parent.is_symlink())
        self.assertEqual(self.other.read_bytes(), before)
        self.assertIn('Skipping linked Windows directory', self.ns['log'].call_args[0][0])

    def test_unreadable_nested_directory_is_logged_and_skipped(self):
        self.ns['log'] = mock.Mock()

        def unreadable(root, followlinks, onerror):
            onerror(PermissionError('unreadable nested directory'))
            return iter(())
        with mock.patch.object(os, 'walk', side_effect=unreadable):
            self.isolate()
        self.assertIn('Skipping unreadable Windows directory', self.ns['log'].call_args[0][0])

    def test_resolved_prefix_in_tool_collection_refuses_before_wine(self):
        source = self.source
        tree = ast.parse(source)
        main = next((n for n in tree.body if isinstance(n, ast.If) and '__main__' in ast.unparse(n.test)))
        guard = next((n for n in main.body if isinstance(n, ast.With) and 'isolate_module_links' in ast.unparse(n)))
        for tool in (self.tool, self.tool.parent / 'other'):
            for component in ('pfx', 'drive_c', 'windows'):
                with self.subTest(tool=tool.name, component=component), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    prefix = Path(temporary) / 'pfx'
                    windows = prefix / 'drive_c/windows'
                    windows.mkdir(parents=True)
                    original = {'pfx': prefix, 'drive_c': windows.parent, 'windows': windows}[component]
                    target = tool / component
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.rmtree(original)
                    original.symlink_to(target, target_is_directory=True)
                    actual = windows.resolve() / 'system32'
                    actual.mkdir(parents=True, exist_ok=True)
                    leaf = actual / 'control.dll'
                    leaf.symlink_to(self.other)
                    inode = leaf.lstat().st_ino
                    self.ns['log'] = mock.Mock()
                    compat = SimpleNamespace(prefix_dir=str(prefix), prefix_lock=nullcontext())
                    compat.isolate_module_links = lambda root: self.ns['isolate_module_links'](compat, root)
                    with self.assertRaises(SystemExit) as stopped:
                        exec(compile(ast.Module(body=[guard], type_ignores=[]), 'proton', 'exec'), dict(g_compatdata=compat, g_proton=SimpleNamespace(base_dir=self.tool), sys=__import__('sys')))
                    self.assertEqual(stopped.exception.code, 1)
                    self.assertTrue(leaf.is_symlink())
                    self.assertEqual(leaf.lstat().st_ino, inode)
                    self.assertIn('Refusing prefix inside compatibility tool directory', self.ns['log'].call_args[0][0])
                    leaf.unlink()

    def test_tool_manifest_outside_collection_refuses_isolation(self):
        other = self.root / 'unregistered-tool'
        other.mkdir()
        (other / 'toolmanifest.vdf').write_text('tool manifest')
        prefix = other / 'files/share/default_pfx'
        leaf = prefix / 'drive_c/windows/system32/control.dll'
        leaf.parent.mkdir(parents=True)
        leaf.symlink_to(self.other)
        self.ns['log'] = mock.Mock()
        result = self.ns['isolate_module_links'](SimpleNamespace(prefix_dir=str(prefix)), self.tool)
        self.assertIs(result, False)
        self.assertTrue(leaf.is_symlink())
        self.assertIn('Refusing prefix inside compatibility tool directory', self.ns['log'].call_args[0][0])

    def test_module_directory_links_into_tools_refuse_isolation(self):
        for tool in (self.tool, self.tool.parent / 'other'):
            for directory in ('system32', 'syswow64'):
                with self.subTest(tool=tool.name, directory=directory), tempfile.TemporaryDirectory(dir=self.root) as temporary:
                    target = tool / 'files/share/default_pfx/drive_c/windows' / directory
                    target.mkdir(parents=True, exist_ok=True)
                    leaf = target / 'nvcuda.dll'
                    leaf.write_bytes(builtin('tool bytes'))
                    before = (leaf.read_bytes(), leaf.stat().st_ino, leaf.stat().st_mtime_ns)
                    prefix = Path(temporary) / 'pfx'
                    windows = prefix / 'drive_c/windows'
                    windows.mkdir(parents=True)
                    link = windows / directory
                    link.symlink_to(target, target_is_directory=True)
                    self.ns['log'] = mock.Mock()
                    result = self.ns['isolate_module_links'](SimpleNamespace(prefix_dir=str(prefix)), self.tool)
                    self.assertIs(result, False)
                    self.assertTrue(link.is_symlink())
                    self.assertEqual((leaf.read_bytes(), leaf.stat().st_ino, leaf.stat().st_mtime_ns), before)
                    self.assertIn('Refusing prefix inside compatibility tool directory', self.ns['log'].call_args[0][0])

@unittest.skipUnless(PROFILE['base'] == 'valve', 'Valve profile')
class ValveRoots(unittest.TestCase):
    method = roots.CudaRoots.method
    test_off_on_off = roots.CudaRoots.test_off_on_off_with_inherited_roots_overrides_and_children
    test_corrupt_root = roots.CudaRoots.test_missing_or_corrupt_roots_fail_closed_before_bootstrap
    test_preserve_override = roots.CudaRoots.test_off_preserves_user_override_and_does_not_warn_for_nvidia_flags

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tool = self.root / 'tool'
        self.cuda = self.tool / 'files/lib/wine-cuda'
        hashes = {}
        for arch in policy.ARCHES.values():
            data = ('fixture GE stub ' + arch).encode()
            path = self.cuda / 'stub' / arch / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            hashes[arch] = hashlib.sha256(data).hexdigest()
            path = self.cuda / 'zluda' / arch / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'fixture builtin PE')
        path = self.cuda / 'zluda/x86_64-unix/nvcuda.so'
        path.parent.mkdir()
        path.write_bytes(b'fixture unixlib')
        path = self.tool / 'files/lib64/libcuda.so.1'
        path.parent.mkdir()
        path.write_bytes(b'fixture backend')
        (self.cuda / 'stubs.json').write_text(json.dumps({'proton_pin': LOCK['proton_base'], 'sha256': hashes}))
        path = self.tool / 'provenance/VALVE-STOCK.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'proton_pin': LOCK['proton_base'], 'nvcuda_sha256': hashes}))

    def test_reference_provider_and_stub_hashes_are_required(self):
        path = self.tool / 'provenance/VALVE-STOCK.json'
        record = json.loads(path.read_text())
        record['proton_pin'] = '0' * 40
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'pinned Valve'):
            policy.select_root({}, self.tool)

    def test_helpers_and_missing_backend_keep_stock_root(self):
        session = SimpleNamespace(env={'PROTON_ENABLE_ZLUDA': '1'}, log_file=None)
        proton = SimpleNamespace(base_dir=self.tool, host_pe_arch='x86_64-windows')
        with mock.patch.object(policy, 'configure_device') as detector:
            policy.configure_launch(session, proton, [], 'LD_LIBRARY_PATH', lambda x: None, bootstrap=True)
            detector.assert_not_called()
        self.assertEqual(session.env['WINEDLLPATH'], str(self.cuda / 'stub'))
        self.assertNotIn('ZLUDA_WINE_BACKEND_PATH', session.env)
        (self.tool / 'files/lib64/libcuda.so.1').unlink()
        policy.configure_launch(session, proton, [], 'LD_LIBRARY_PATH', lambda x: None)
        self.assertEqual(session.env['PROTON_ENABLE_ZLUDA'], '0')
        self.assertEqual(session.env['WINEDLLPATH'], str(self.cuda / 'stub'))


@unittest.skipUnless(PROFILE['base'] == 'valve', 'Valve profile')
class ValveSelection(unittest.TestCase):
    def test_stock_waitforexitandrun_dispatches_preloader_wine_and_steam(self):
        tree = ast.parse((PROTON / 'proton').read_text())
        session = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Session')
        run = next(n for n in session.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
        main = next(n for n in tree.body if isinstance(n, ast.If) and '__main__' in ast.unparse(n.test))
        branch = next(n for n in ast.walk(main) if isinstance(n, ast.If) and
                      ast.unparse(n.test) == "sys.argv[1] == 'waitforexitandrun'")
        self.assertEqual([ast.unparse(n) for n in branch.body],
                         ["g_session.run_proc([g_proton.wineserver_bin, '-w'])", 'rc = g_session.run()'])
        arguments = [r'Z:\game\probe.exe', '--expect-stub']
        ns = {'os': SimpleNamespace(environ={'SteamGameId': '999999'}),
              'sys': SimpleNamespace(argv=['proton', 'waitforexitandrun', *arguments]),
              'shutil': SimpleNamespace(which=lambda name: None),
              'g_proton': SimpleNamespace(host_pe_arch='x86_64-windows', lib_dir='/tool/files/lib/')}
        exec(compile(ast.Module(body=[run], type_ignores=[]), '<shipping Session.run>', 'exec'), ns)
        commands = []
        instance = SimpleNamespace(env={}, remote_debug_cmd=None, cmdlineappend=[],
                                   run_proc=lambda argv: commands.append(argv) or 17)
        self.assertEqual(ns['run'](instance), 17)
        self.assertEqual([Path(arg) for arg in commands[0][:2]],
                         [Path('/tool/files/lib/wine/x86_64-unix') / name for name in ['wine-preloader', 'wine']])
        self.assertEqual(commands[0][2:], [r'c:\windows\system32\steam.exe', *arguments])
        self.assertEqual(instance.env['WINELOADERNOEXEC'], '1')

    def test_native_control_requires_seed_provenance_and_exact_source_and_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / 'libcuda.so.1'
            binary.write_bytes(b'final backend fixture')
            control = {'backend': LOCK['components']['zluda'], 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                       'run': 123, 'artifact': 456, 'executor': 'a' * 40, 'zip_sha256': 'b' * 64,
                       **{key: LOCK[key] for key in ('sdk_image', 'llvm', 'rust_version')}}
            with mock.patch.dict(LOCK, native_control=control):
                self.assertEqual(native_control(binary), control)
            for key, value in [('backend', '0' * 40), ('sha256', None), ('sha256', 'c' * 64),
                               ('run', None), ('artifact', 0), ('executor', ''), ('zip_sha256', ''),
                               ('sdk_image', 'wrong-sdk'), ('llvm', '0' * 40), ('rust_version', '0.0.0')]:
                with self.subTest(field=key, value=value), mock.patch.dict(LOCK, native_control=dict(control, **{key: value})):
                    with self.assertRaises(ValueError):
                        native_control(binary)

    def test_five_titles_settings_and_pci_precedence(self):
        hardware = [([(0x030000,0x1002)],'1'), ([(0x030200,0x10de)],'0'),
                    ([(0x038000,0x8086)],'0'), ([], '0'),
                    ([(0x030000,0x1002),(0x030200,0x10de)],'0'),
                    ([(0x030000,0x1002),(0x040300,0x10de)],'1'),
                    ([('invalid',0x1002)],'0'), (None,'0')]
        with tempfile.TemporaryDirectory() as temporary:
            for i,(devices,default) in enumerate(hardware):
                directory = Path(temporary)/str(i)
                if devices is not None:directory.mkdir()
                for j,(cls,vendor) in enumerate(devices or []):
                    device=directory/str(j);device.mkdir()
                    (device/'class').write_text(hex(cls) if isinstance(cls,int) else cls);(device/'vendor').write_text(hex(vendor))
                for app in policy.TITLE_DEPENDENCIES:
                    for explicit in (None,'0','1'):
                        for setting in (None,'0','1',0,1):
                            env={'SteamAppId':app}
                            if explicit is not None:env['PROTON_ENABLE_ZLUDA']=explicit
                            settings={} if setting is None else {'PROTON_ENABLE_ZLUDA':setting}
                            expected=explicit if explicit is not None else str(setting) if setting is not None else default
                            with self.subTest(app=app,devices=devices,explicit=explicit,setting=setting), \
                                 mock.patch.dict(os.environ,env,clear=True), \
                                 mock.patch.dict(sys.modules,{'user_settings':SimpleNamespace(user_settings=settings),'utilities':None,'vulkan':None}), \
                                 mock.patch.object(policy,'Path',return_value=directory):
                                policy.early_title_policy(os.environ,'run')
                                self.assertEqual(os.environ['PROTON_ENABLE_ZLUDA'],expected)
                                self.assertEqual(os.environ.get('PROTON_USE_WOW64'),'1' if expected=='1' and app in policy.PE32_TITLES else None)
        env={'SteamAppId':'49520','PROTON_ENABLE_ZLUDA':'1'}
        policy.early_title_policy(env,'runinprefix')
        self.assertNotIn('PROTON_USE_WOW64',env)

    def test_deck_hsa_predicate_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);dmi=root/'dmi';dmi.mkdir();nodes=root/'nodes';nodes.mkdir()
            for name,value in [('sys_vendor','Valve'),('product_name','Jupiter'),('board_name','Jupiter')]:
                (dmi/name).write_text(value)
            def path(name):
                return dmi if name=='/sys/class/dmi/id' else nodes
            def targets(values):
                for node in nodes.iterdir():shutil.rmtree(node)
                for i,value in enumerate(values):
                    node=nodes/str(i);node.mkdir();(node/'properties').write_text('gfx_target_version '+str(value)+'\n')
            for values in ([0,100303],[100303],[100303,100303],[100302],[],['bad'],['100303 trailing'],['-0',100303]):
                targets(values)
                for enabled in ('0','1'):
                    env={'PROTON_ENABLE_ZLUDA':enabled}
                    with mock.patch.object(policy,'Path',side_effect=path):policy.configure_device(env)
                    self.assertEqual(env.get('HSA_OVERRIDE_GFX_VERSION'),'10.3.0' if enabled=='1' and values in ([0,100303],[100303]) else None)
            targets([100303]);(dmi/'board_name').unlink()
            env={'PROTON_ENABLE_ZLUDA':'1'}
            with mock.patch.object(policy,'Path',side_effect=path):policy.configure_device(env)
            self.assertNotIn('HSA_OVERRIDE_GFX_VERSION',env)
            (dmi/'board_name').write_text('Galileo')
            targets([100303]);(dmi/'sys_vendor').write_text('Other')
            env={'PROTON_ENABLE_ZLUDA':'1'}
            with mock.patch.object(policy,'Path',side_effect=path):policy.configure_device(env)
            self.assertNotIn('HSA_OVERRIDE_GFX_VERSION',env)
            env['HSA_OVERRIDE_GFX_VERSION']='user'
            policy.configure_device(env)
            self.assertEqual(env['HSA_OVERRIDE_GFX_VERSION'],'user')

    def test_admission_uses_selected_gitlink_and_refuses_wrong_paths(self):
        with mock.patch('inputs.git',return_value=PROFILE['source_commit']):check_profile_source()
        with mock.patch('inputs.git',return_value='0'*40),self.assertRaisesRegex(ValueError,'gitlink'):check_profile_source()
        with mock.patch.dict(PROFILE,source_path='../proton-source'),self.assertRaisesRegex(ValueError,'source path'):check_profile_source()

    def test_early_and_guard_precede_any_prefix_bootstrap(self):
        source=(PROTON/'proton').read_text()
        self.assertLess(source.index('proton_zluda.early_title_policy(os.environ'),source.index('g_session.init_wine()'))
        self.assertLess(source.index('g_compatdata.isolate_module_links(g_proton.base_dir)'),source.index('proton_zluda.bootstrap(g_session'))
        self.assertIn('sys.exit(1)',source[source.index('if g_compatdata.isolate_module_links'):])
        self.assertNotIn('import utilities',source)
        self.assertNotIn('import vulkan',source)

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
