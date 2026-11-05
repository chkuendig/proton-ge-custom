"""Prefix-copy policy and actual in-place-writer counterfactuals; no Wine here."""
import ast
from contextlib import nullcontext
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROTON = Path(__import__('os').environ.get('PROTON_SOURCE_ROOT', ROOT / 'proton-source')).resolve()
BASE = 'd013499f64f73f22e27ede7437a6fd511974c36a'


def functions(source):
    tree = ast.parse(source)
    ns = dict(os=os, Path=Path, uuid=uuid, shutil=shutil, stat=stat,
              copyfile=shutil.copyfile, copyfile_reflink=shutil.copyfile, copy_file_range=os.copy_file_range, errno=__import__('errno'), log=lambda *x: None,
              file_exists=lambda p, follow_symlinks=True: os.path.exists(p) if follow_symlinks else os.path.lexists(p))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('file_is_wine_builtin_dll', 'copyfile_fds', 'atomic_copy', 'try_copy', 'try_copyfile'):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'proton', 'exec'), ns)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CompatData')
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('pfx_copy', 'isolate_module_links', 'create_symlink'):
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'proton', 'exec'), ns)
    setup = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'setup_prefix')
    icu = next(n for n in ast.walk(setup) if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == 'icufiles')
    ns['icu_code'] = compile(ast.Module(body=[icu], type_ignores=[]), 'proton', 'exec')
    return ns


def builtin(label):
    return b'MZ' + bytes(62) + b'Wine builtin DLL\0' + label.encode()


def inplace(path, data):
    # Same inode operation as Wine create_dest_file, not a replacement helper.
    with path.open('r+b') as f:
        f.seek(64)
        assert f.read(20).startswith(b'Wine builtin DLL')
        f.seek(0)
        f.truncate()
        f.write(data)


class Isolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (PROTON / 'proton').read_text()
        cls.old_source = subprocess.check_output(['git', '-C', str(PROTON), 'show', BASE + ':proton'], text=True)

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.tool = self.root / 'compatibilitytools.d/ours'
        self.other = self.root / 'other/files/lib/wine/x86_64-windows/control.dll'
        self.other.parent.mkdir(parents=True)
        self.other.write_bytes(builtin('other bytes'))
        self.own = self.tool / 'files/lib/wine/x86_64-windows/control.dll'
        self.own.parent.mkdir(parents=True)
        self.own.write_bytes(builtin('our bytes'))
        self.prefix = self.root / 'compatdata/0/pfx'
        self.dst = self.prefix / 'drive_c/windows/system32/control.dll'
        self.dst.parent.mkdir(parents=True)
        self.ns = functions(self.source)
        self.obj = SimpleNamespace(prefix_dir=str(self.prefix) + '/')

    def isolate(self):
        self.ns['isolate_module_links'](self.obj, self.tool)

    def test_unfixed_reproduces_symlink_and_hardlink_overwrite(self):
        for link in ('symlink', 'hardlink'):
            self.other.write_bytes(builtin('other bytes'))
            if link == 'symlink':self.dst.symlink_to(self.other)
            else:os.link(self.other, self.dst)
            inplace(self.dst, builtin('overwritten'))
            self.assertEqual(self.other.read_bytes(), builtin('overwritten'))
            self.dst.unlink()

    def test_foreign_aliases_are_private_before_writer_in_all_module_directories(self):
        for directory in ('system32', 'syswow64', 'winsxs/amd64_fixture'):
            for link in ('symlink', 'hardlink'):
                path = self.prefix / 'drive_c/windows' / directory / 'control.dll'
                path.parent.mkdir(parents=True, exist_ok=True)
                if link == 'symlink':path.symlink_to(self.other)
                else:os.link(self.other, path)
                before = self.other.read_bytes()
                self.isolate()
                self.assertFalse(path.is_symlink())
                self.assertNotEqual(path.stat().st_ino, self.other.stat().st_ino)
                inplace(path, builtin('updated'))
                self.assertEqual(self.other.read_bytes(), before)
                path.unlink()

    def test_broken_foreign_builtin_link_is_dropped(self):
        self.dst.symlink_to(self.other)
        self.other.unlink()
        self.isolate()
        self.assertFalse(os.path.lexists(self.dst))

    def test_explicit_own_symlink_and_native_user_override_are_preserved(self):
        self.dst.symlink_to(self.own)
        native = self.dst.with_name('game-native.dll')
        target = self.root / 'game-native.dll'
        target.write_bytes(b'native user DLL without a builtin marker')
        native.symlink_to(target)
        self.isolate()
        self.assertTrue(self.dst.is_symlink())  # documented old-prefix boundary
        self.assertTrue(native.is_symlink())

    def test_default_copy_mode_prevents_new_inbound_aliases_even_during_initial_copy(self):
        source = self.root / 'template/control.dll'
        source.parent.mkdir()
        source.symlink_to(self.own)
        for text, expected_link in ((self.old_source, True), (self.source, False)):
            ns = functions(text)
            with mock.patch.dict(os.environ, {'PROTON_DLL_COPY': '*'}):
                ns['pfx_copy'](self.obj, str(source), str(self.dst))
            self.assertEqual(self.dst.is_symlink(), expected_link)
            original = self.own.read_bytes()
            inplace(self.dst, builtin('other tool update'))
            self.assertEqual(self.own.read_bytes() == original, not expected_link)
            self.dst.unlink()
            self.own.write_bytes(builtin('our bytes'))

    def test_icu_trio_is_copied_for_both_arches(self):
        for text, expected_link in ((self.old_source, True), (self.source, False)):
            ns = functions(text)
            for arch in ('i386-windows', 'x86_64-windows'):
                d = self.tool / 'files/lib/wine/icu' / arch
                d.mkdir(parents=True, exist_ok=True)
                for name in ('icuin68', 'icuuc68', 'icudt68'):(d / (name + '.dll')).write_bytes(b'ICU native fixture')
            proto = SimpleNamespace(arch_pe_dir=lambda d, wow: str(self.tool / 'files/lib' / d / ('i386-windows' if wow else 'x86_64-windows')) + '/')
            (self.prefix / 'drive_c/windows/syswow64').mkdir(exist_ok=True)
            self.obj.create_symlink = lambda *args:ns['create_symlink'](self.obj, *args)
            ns.update(self=self.obj, g_proton=proto, icufiles=['icuin68','icuuc68','icudt68'], tracked_files=io.StringIO())
            exec(ns['icu_code'], ns)
            for d in ('system32','syswow64'):
                for name in ns['icufiles']:
                    p=self.prefix/'drive_c/windows'/d/(name+'.dll')
                    self.assertEqual(p.is_symlink(), expected_link)
                    self.assertEqual(p.read_bytes(), b'ICU native fixture')
                    p.unlink()

    def test_icu_foreign_native_symlink_is_detached(self):
        path = self.dst.with_name('icuin68.dll')
        target = self.other.with_name('icuin68.dll');target.write_bytes(b'ICU without a Wine marker')
        path.symlink_to(target)
        self.isolate()
        self.assertFalse(path.is_symlink())
        self.assertEqual(path.read_bytes(), target.read_bytes())

    def test_linked_nested_parent_is_logged_and_skipped(self):
        self.dst.parent.rmdir()
        self.dst.parent.symlink_to(self.other.parent)
        # The nested directory target remains outside the isolation walk.
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
        main = next(n for n in tree.body if isinstance(n, ast.If) and '__main__' in ast.unparse(n.test))
        guard = next(n for n in main.body if isinstance(n, ast.With) and 'isolate_module_links' in ast.unparse(n))
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
                        exec(compile(ast.Module(body=[guard], type_ignores=[]), 'proton', 'exec'),
                             dict(g_compatdata=compat, g_proton=SimpleNamespace(base_dir=self.tool), sys=__import__('sys')))
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

    def test_copy_failure_preserves_alias_and_tool_bytes(self):
        self.dst.symlink_to(self.other)
        before = self.other.read_bytes()
        def fail_copy(*args):
            raise OSError('copy failed')
        self.ns['copy_file_range'] = fail_copy
        with self.assertRaisesRegex(OSError, 'copy failed'):self.isolate()
        self.assertTrue(self.dst.is_symlink())
        self.assertEqual(self.other.read_bytes(), before)
        self.assertEqual(list(self.dst.parent.glob('*.tmp')), [])

    def test_atomic_copy_falls_back_after_partial_range_copy(self):
        import errno
        source = self.root / 'source.dll'
        source.write_bytes(b'complete source contents')
        calls = []
        def cross_device(src, dst, count):
            calls.append(count)
            if len(calls) == 1:
                return os.copy_file_range(src, dst, 5)
            raise OSError(errno.EXDEV, 'cross-device copy')
        self.ns['copy_file_range'] = cross_device
        self.ns['try_copyfile'](str(source), str(self.dst))
        self.assertEqual(self.dst.read_bytes(), source.read_bytes())
        self.assertEqual(len(calls), 2)

    def test_concurrent_symlink_replacement_cannot_redirect_copy(self):
        old = subprocess.check_output(['git', '-C', str(PROTON), 'show', '0bda91ad:proton'], text=True)
        source = self.root / 'source.dll'
        source.write_bytes(b'new copy')
        for text, safe in ((old, False), (self.source, True)):
            for name in ('try_copy', 'try_copyfile'):
                with self.subTest(safe=safe, helper=name):
                    self.dst.unlink(missing_ok=True)
                    self.dst.write_bytes(b'old destination')
                    self.other.write_bytes(b'foreign tool')
                    ready, installed = threading.Event(), threading.Event()
                    def actor():
                        self.assertTrue(ready.wait(5))
                        self.dst.unlink(missing_ok=True)
                        self.dst.symlink_to(self.other)
                        installed.set()
                    thread = threading.Thread(target=actor)
                    thread.start()
                    ns = functions(text)
                    original_copy = shutil.copyfile
                    original_range = os.copy_file_range
                    def rendezvous(*args, **kwargs):
                        ready.set()
                        if not installed.wait(5):
                            raise RuntimeError('actor timed out')
                        return (original_range if safe else original_copy)(*args, **kwargs)
                    if safe:
                        ns['copy_file_range'] = rendezvous
                        ns[name](str(source), str(self.dst))
                    else:
                        ns['copyfile'] = rendezvous
                        ns[name](str(source), str(self.dst))
                    thread.join(5)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(self.other.read_bytes(), b'foreign tool' if safe else b'new copy')
                    if safe:
                        self.assertEqual(self.dst.read_bytes(), b'new copy')
                        self.assertFalse(self.dst.is_symlink())

    def test_preflight_is_unconditional_before_bootstrap_and_runinprefix(self):
        tree=ast.parse(self.source)
        main=next(n for n in tree.body if isinstance(n, ast.If) and '__main__' in ast.unparse(n.test))
        guard=next(n for n in main.body if isinstance(n, ast.With) and 'isolate_module_links' in ast.unparse(n))
        boot=next(n for n in main.body if 'proton_zluda.bootstrap' in ast.unparse(n))
        session=next(n for n in main.body if 'g_session.init_session' in ast.unparse(n))
        default=next(n for n in main.body if isinstance(n, ast.Expr) and 'setdefault' in ast.unparse(n))
        self.assertLess(main.body.index(default), main.body.index(guard))
        self.assertLess(main.body.index(guard), main.body.index(boot))
        self.assertLess(main.body.index(guard), main.body.index(session))
        self.dst.symlink_to(self.other)
        compat=SimpleNamespace(prefix_lock=nullcontext(),isolate_module_links=lambda root:self.isolate())
        for mode in ('run','waitforexitandrun','runinprefix'):
            exec(compile(ast.Module(body=[guard],type_ignores=[]),'proton','exec'),
                 dict(g_compatdata=compat,g_proton=SimpleNamespace(base_dir=self.tool)))
            self.assertFalse(self.dst.is_symlink(), mode)
        with mock.patch.dict(os.environ,{},clear=True):
            exec(compile(ast.Module(body=[default],type_ignores=[]),'proton','exec'),{'os':os})
            self.assertEqual(os.environ['PROTON_DLL_COPY'],'*')


if __name__ == '__main__':unittest.main()
