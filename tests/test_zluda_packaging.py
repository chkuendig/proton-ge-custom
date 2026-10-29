import copy
import json
import os
import shutil
from unittest import mock
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import inputs
import overlay


class PayloadManifest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'lib').mkdir()
        (self.root / 'lib/a.so.1').write_bytes(b'payload')
        (self.root / 'lib/a.so').symlink_to('a.so.1')
        (self.root / 'lib64').symlink_to('lib', target_is_directory=True)

    def test_regular_and_both_symlink_kinds(self):
        inputs.manifest(self.root)
        inputs.verify_manifest(self.root)
        self.assertEqual(json.loads((self.root / 'SYMLINKS.json').read_text()),
                         {'lib/a.so': 'a.so.1', 'lib64': 'lib'})
        self.assertIn('lib/a.so.1', (self.root / 'MANIFEST').read_text())

    def test_changed_missing_and_extra_file(self):
        for change in ['changed', 'missing', 'extra']:
            with self.subTest(change=change):
                inputs.manifest(self.root)
                path = self.root / 'lib/a.so.1'
                if change == 'changed':
                    path.write_bytes(b'corrupted')
                elif change == 'missing':
                    path.unlink()
                else:
                    (self.root / 'extra').write_bytes(b'surprise')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    inputs.verify_manifest(self.root)
                path.write_bytes(b'payload')

    def test_alias_retarget_with_same_bytes_is_rejected(self):
        (self.root / 'lib/b.so.1').write_bytes(b'payload')
        inputs.manifest(self.root)
        alias = self.root / 'lib/a.so'
        alias.unlink()
        alias.symlink_to('b.so.1')
        with self.assertRaisesRegex(ValueError, 'symlink inventory'):
            inputs.verify_manifest(self.root)

    def test_external_and_broken_links_rejected(self):
        for target in ['/etc/passwd', 'missing']:
            alias = self.root / 'lib/a.so'
            alias.unlink()
            alias.symlink_to(target)
            with self.assertRaises(ValueError):
                inputs.manifest(self.root)

    def test_manifest_cannot_escape(self):
        inputs.manifest(self.root)
        (self.root / 'MANIFEST').write_text('0' * 64 + '  ../outside\n')
        with self.assertRaisesRegex(ValueError, 'escapes root'):
            inputs.verify_manifest(self.root)


class RecursivePins(unittest.TestCase):
    def test_nested_gitlink_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent, child, grand = [Path(temporary) / p for p in ['parent', 'child', 'grand']]
            def run(root, *args):
                return subprocess.check_output(['git', '-C', str(root), '-c', 'protocol.file.allow=always',
                                                '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                                *args], stderr=subprocess.DEVNULL).decode().strip()
            for repo in [parent, child, grand]:
                repo.mkdir()
                run(repo, 'init', '-q')
                (repo / 'file').write_text('first')
                run(repo, 'add', '.')
                run(repo, 'commit', '-qm', 'fixture')
            run(child, 'submodule', 'add', str(grand), 'nested')
            run(child, 'commit', '-qam', 'nested pin')
            run(parent, 'submodule', 'add', str(child), 'child')
            run(parent, 'commit', '-qam', 'child pin')
            run(parent, 'submodule', 'update', '--init', '--recursive')
            inputs.check_tree(parent)
            nested = parent / 'child/nested'
            (nested / 'file').write_text('second')
            run(nested, 'commit', '-qam', 'wrong HEAD')
            with self.assertRaisesRegex(ValueError, 'dirty component|gitlink mismatch'):
                inputs.check_tree(parent)


class PackageContract(unittest.TestCase):
    def test_job_limit_rejects_before_source_or_build(self):
        for jobs in ['0', '4', '100', '-1']:
            env = dict(os.environ, JOBS=jobs)
            for name in ['SOURCE_ROOT', 'SDK_ROOT', 'DESTDIR', 'WINE_SDK_ROOT', 'BUILD_ROOT', 'DOWNLOAD_ROOT']:
                env[name] = '/nonexistent-packaging-fixture'
            result = subprocess.run(['bash', str(ROOT / 'packaging/zluda-overlay/build-components.sh')],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('JOBS must be 1..3', result.stderr)

    def test_redist_copies_launcher_helper(self):
        text = (ROOT / 'Makefile.in').read_text()
        start = text.index('DIST_COPY_TARGETS :=')
        block = text[start:text.index('\n\n', start)]
        self.assertIn('$(PROTON_ZLUDA_PY_TARGET)', block)
        self.assertIn('PROTON_ZLUDA_PY_TARGET := $(addprefix $(DST_BASE)/,proton_zluda.py)', text)
        self.assertIn('$(DIST_COPY_TARGETS): | $(DST_DIR)\n\tcp -a $(SRCDIR)/$(notdir $@) $@', text)

    def test_explicit_runtime_and_required_architectures(self):
        self.assertEqual(len(inputs.LOCK['rocm_runtime']), 7)
        for name in inputs.LOCK['rocm_runtime']:
            self.assertNotIn('*', name)
        required = overlay.required_payload()
        for name in ['files/lib/wine/i386-windows/nvcuda.dll',
                     'files/lib/wine/x86_64-unix/nvcuda.so',
                     'files/lib/wine/nvapi/i386-windows/nvapi.dll', 'proton_zluda.py']:
            self.assertIn(name, required)

    def test_launcher_loader_order_is_preserved(self):
        text = (ROOT / 'proton').read_text()
        start = text.index('if zluda_enabled(g_session.env):')
        block = text[start:text.index('#determine mode', start)]
        self.assertLess(block.index('zluda_lib_dir, ":"'), block.index('wine/x86_64-unix'))
        self.assertIn('g_proton.path("files/lib64/")', block)


class AssemblyFixture(unittest.TestCase):
    def test_complete_assembly_and_missing_payload_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, cache, build = [root / n for n in ['source', 'cache', 'build']]
            for path in [source, cache, build]:
                path.mkdir()
            def run(repo, *args):
                return subprocess.check_output(['git', '-C', str(repo), '-c',
                                                'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                                *args], stderr=subprocess.DEVNULL).decode().strip()
            run(source, 'init', '-q')
            (source / 'proton').write_text('launcher')
            (source / 'proton_zluda.py').write_text('helper')
            pf = source / 'protonfixes'
            pf.mkdir()
            run(pf, 'init', '-q')
            (pf / 'changed.py').write_text('base')
            (pf / 'deleted.py').write_text('old')
            run(pf, 'add', '.')
            run(pf, 'commit', '-qm', 'base')
            run(source, 'add', '.')
            run(source, 'commit', '-qm', 'base tool')
            base = run(source, 'rev-parse', 'HEAD')
            (pf / 'changed.py').write_text('new')
            (pf / 'deleted.py').unlink()
            run(pf, 'add', '-A')
            run(pf, 'commit', '-qm', 'new fixes')
            run(source, 'add', 'protonfixes')
            run(source, 'commit', '-qm', 'new tool')
            commit = run(source, 'rev-parse', 'HEAD')
            lock = copy.deepcopy(inputs.LOCK)
            lock['components']['protonfixes'] = run(pf, 'rev-parse', 'HEAD')
            lock['ge_source'] = base
            ge = root / 'ge'
            ge.mkdir()
            tool = ge / 'GE-Proton11-7'
            (tool / 'protonfixes').mkdir(parents=True)
            (tool / 'protonfixes/deleted.py').write_text('old')
            (tool / 'compatibilitytool.vdf').write_text(
                '\n"GE-Proton11-7" // Internal name\n"display_name" "GE"\n')
            (cache / 'GE-Proton11-7-x86_64.tar.gz').write_bytes(b'fake GE for extraction fixture')
            item = next(x for x in lock['downloads'] if x['id'] == 'ge')
            item['sha256'] = inputs.sha256(cache / 'GE-Proton11-7-x86_64.tar.gz')
            overlays = root / 'overlays'
            overlays.mkdir()
            trees = {}
            for part in ['zluda', 'bridges']:
                tree = root / ('tree-' + part)
                (tree / 'provenance').mkdir(parents=True)
                record = {'part': part, 'proton': commit,
                          'input_lock_sha256': inputs.sha256(inputs.HERE / 'inputs.lock.json')}
                (tree / 'provenance' / (part + '.json')).write_text(json.dumps(record))
                for name in overlay.required_payload():
                    if name in ['proton', 'proton_zluda.py']:
                        continue
                    if (name.startswith('files/lib64/')) != (part == 'zluda'):
                        continue
                    path = tree / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'component')
                if part == 'zluda':
                    (tree / 'files/lib64/wine').symlink_to('../lib/wine')
                    (tree / 'files/lib/wine/x86_64-unix').mkdir(parents=True)
                inputs.manifest(tree)
                trees[part + '.tar.gz'] = tree
            def extract(archive, target, ge_base=False):
                shutil.copytree(ge if archive.name.startswith('GE-') else trees[archive.name],
                                target, symlinks=True)
            env = {'SOURCE_ROOT': str(source), 'DOWNLOAD_ROOT': str(cache),
                   'BUILD_ROOT': str(build), 'DESTDIR': str(root / 'result')}
            with mock.patch.dict(os.environ, env), mock.patch.object(overlay, 'LOCK', lock), \
                 mock.patch.object(overlay, 'extract', extract):
                overlay.assemble(overlays)
                dest = root / 'result'
                inputs.verify_manifest(dest)
                self.assertEqual((dest / 'protonfixes/changed.py').read_text(), 'new')
                self.assertFalse((dest / 'protonfixes/deleted.py').exists())
                self.assertEqual((dest / 'proton_zluda.py').read_text(), 'helper')
                self.assertIn('GE-Proton11-7-zluda', (dest / 'compatibilitytool.vdf').read_text())
                shutil.rmtree(dest)
                shutil.rmtree(build)
                build.mkdir()
                (trees['bridges.tar.gz'] / 'files/lib/wine/i386-windows/nvcuda.dll').unlink()
                inputs.manifest(trees['bridges.tar.gz'])
                with self.assertRaisesRegex(AssertionError, 'i386-windows/nvcuda.dll'):
                    overlay.assemble(overlays)


if __name__ == '__main__':
    unittest.main()
