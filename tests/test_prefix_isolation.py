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
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASE = 'd013499f64f73f22e27ede7437a6fd511974c36a'


def functions(source):
    tree = ast.parse(source)
    ns = dict(os=os, Path=Path, uuid=uuid, shutil=shutil, stat=stat,
              copyfile=shutil.copyfile, errno=__import__('errno'), log=lambda *x: None,
              file_exists=lambda p, follow_symlinks=True: os.path.exists(p) if follow_symlinks else os.path.lexists(p))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ('file_is_wine_builtin_dll', 'try_copy', 'try_copyfile'):
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
        cls.source = (ROOT / 'proton').read_text()
        cls.old_source = subprocess.check_output(['git', '-C', str(ROOT), 'show', BASE + ':proton'], text=True)

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.tool = self.root / 'ours'
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

    def test_linked_parent_requires_refusal_not_leaf_copy(self):
        self.dst.parent.rmdir()
        self.dst.parent.symlink_to(self.other.parent)
        # Without refusal the writer reaches another tree even with private leaf policy.
        inplace(self.dst, builtin('unfixed parent write'))
        self.assertEqual(self.other.read_bytes(), builtin('unfixed parent write'))
        before = self.other.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'linked Windows directory'):
            self.isolate()
        self.assertEqual(self.other.read_bytes(), before)

    def test_copy_failure_preserves_alias_and_tool_bytes(self):
        self.dst.symlink_to(self.other)
        before = self.other.read_bytes()
        def fail(src, dst):
            Path(dst).write_bytes(b'partial')
            raise OSError('copy failed')
        self.ns['copyfile'] = fail
        with self.assertRaisesRegex(OSError, 'copy failed'):self.isolate()
        self.assertTrue(self.dst.is_symlink())
        self.assertEqual(self.other.read_bytes(), before)
        self.assertEqual(list(self.dst.parent.glob('*.tmp')), [])

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
