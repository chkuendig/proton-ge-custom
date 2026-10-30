"""Exact upstream/fork tree proofs and isolated patch preparation; no builds."""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import component_patches as patches
from inputs import LOCK, sha256
import inputs


def git(repo, *args, data=None):
    return subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=Christian Kündig',
        '-c', 'user.email=christian@kuendig.info', '-c', 'commit.gpgsign=false',
        '-c', 'core.hooksPath=/dev/null', *args], input=data, stderr=subprocess.PIPE).decode().strip()


class ComponentPatches(unittest.TestCase):
    def test_upstream_plus_our_patches_equals_the_complete_original_fork_trees(self):
        fixture = json.loads((ROOT / 'tests/fixtures/component-patch-trees.json').read_text())
        for name, snapshot in fixture.items():
            with self.subTest(component=name), tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary)
                git(repo, 'init', '-q')
                for oid, content in snapshot['blobs'].items():
                    self.assertEqual(git(repo, 'hash-object', '-w', '--stdin', data=base64.b64decode(content)), oid)
                index = ''.join(mode + ' ' + oid + '\t' + path + '\0' for mode, oid, path in snapshot['entries'])
                git(repo, 'update-index', '-z', '--index-info', data=index.encode())
                self.assertEqual(git(repo, 'write-tree', '--missing-ok'), snapshot['upstream_tree'])
                self.assertEqual(LOCK['components'][name], snapshot['upstream_commit'])
                spec = LOCK['component_patches'][name]
                self.assertEqual(spec['upstream_tree'], snapshot['upstream_tree'])
                self.assertEqual((spec['fork_commit'], spec['fork_tree']),
                                 (snapshot['fork_commit'], snapshot['fork_tree']))
                series = patches.series(ROOT, name)
                own = [entry for entry in series if entry['fork_change']]
                ge = [entry for entry in series if not entry['fork_change']]
                patches.apply_series(repo, ROOT, own, cached=True)
                self.assertEqual(git(repo, 'write-tree', '--missing-ok'), snapshot['fork_tree'])
                # Preserve GE's OptiScaler patches too. Applying GE then our
                # changes must equal GE applied on the original fork tree.
                patches.apply_series(repo, ROOT, ge, cached=True)
                expected = git(repo, 'write-tree', '--missing-ok')
                self.assertEqual(expected, spec['prepared_tree'])
                git(repo, 'read-tree', snapshot['upstream_tree'])
                patches.apply_series(repo, ROOT, series, cached=True)
                self.assertEqual(git(repo, 'write-tree', '--missing-ok'), expected)

    def test_patch_hash_inventory_is_closed_and_ordered(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / 'patches/dxvk-nvapi', root / 'patches/dxvk-nvapi')
            entries = patches.series(root, 'dxvk-nvapi')
            first = root / entries[0]['path']
            saved = first.read_bytes()
            first.write_bytes(saved + b'changed\n')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                patches.series(root, 'dxvk-nvapi')
            first.write_bytes(saved)
            extra = first.parent / '9999-unlocked.patch'
            extra.write_bytes(saved)
            with self.assertRaisesRegex(ValueError, 'inventory'):
                patches.series(root, 'dxvk-nvapi')
            extra.unlink()
            lock = copy.deepcopy(LOCK)
            lock['component_patches']['dxvk-nvapi']['patches'].reverse()
            with self.assertRaisesRegex(ValueError, 'inventory'):
                patches.series(root, 'dxvk-nvapi', lock)
            first.unlink()
            first.symlink_to(ROOT / entries[0]['path'])
            with self.assertRaisesRegex(ValueError, 'checksum'):
                patches.series(root, 'dxvk-nvapi')

    def test_format_patch_retains_each_original_author_date_and_message(self):
        fixture = json.loads((ROOT / 'tests/fixtures/component-patch-trees.json').read_text())
        for component in fixture.values():
            for original in component['commits']:
                with self.subTest(commit=original['commit']), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    mail = ROOT / original['patch']
                    self.assertTrue(mail.read_text().startswith('From ' + original['commit'] + ' '))
                    info = subprocess.check_output(['git', 'mailinfo', str(root / 'message'), str(root / 'diff')],
                                                   input=mail.read_bytes()).decode()
                    headers = dict(line.split(': ', 1) for line in info.splitlines() if ': ' in line)
                    for key in ['Author', 'Email', 'Date', 'Subject']:
                        self.assertEqual(headers[key], original[key.lower()])
                    self.assertEqual((root / 'message').read_text().rstrip('\n'), original['body'])

    def test_full_make_bridge_admits_ge_prep_but_rejects_changed_bridge_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git(root, 'init', '-q')
            child = root / 'zluda'
            (child / 'crate').mkdir(parents=True)
            git(child, 'init', '-q')
            (child / 'crate/Cargo.lock').write_text('locked bridge\n')
            git(child, 'add', '.')
            git(child, 'commit', '-qm', 'bridge')
            (root / 'launcher').write_text('GE base\n')
            git(root, 'add', '.')
            git(root, 'commit', '-qm', 'base')
            lock = {'proton_base': git(root, 'rev-parse', 'HEAD'),
                    'components': {'zluda': git(child, 'rev-parse', 'HEAD')},
                    'component_patches': {}, 'wine_bridge': {'path': 'crate',
                        'tree': git(child, 'rev-parse', 'HEAD:crate'),
                        'cargo_lock_sha256': sha256(child / 'crate/Cargo.lock')}}
            path = root / 'packaging/zluda-overlay/inputs.lock.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(lock))
            git(root, 'add', '.')
            git(root, 'commit', '-qm', 'lock')
            # Real GE prep changes unrelated launcher/helper/component files.
            (root / 'launcher').write_text('prepared GE helper\n')
            with mock.patch.object(inputs, 'LOCK', lock):
                inputs.check_bridge_sources(root)
                path.write_text(json.dumps(lock) + '\n')
                with self.assertRaisesRegex(ValueError, 'dirty bridge input lock'):
                    inputs.check_bridge_sources(root)
                path.write_text(json.dumps(lock))
                (child / 'crate/Cargo.lock').write_text('tampered bridge\n')
                with self.assertRaisesRegex(ValueError, 'dirty Wine bridge'):
                    inputs.check_bridge_sources(root)

    def fixture(self, root):
        source = root / 'source'
        source.mkdir()
        git(source, 'init', '-q')
        vendor = root / 'vendor'
        vendor.mkdir()
        git(vendor, 'init', '-q')
        (vendor / 'header.h').write_text('vendor pin\n')
        git(vendor, 'add', '.')
        git(vendor, 'commit', '-qm', 'vendor')
        child = source / 'dxvk-nvapi'
        child.mkdir()
        git(child, 'init', '-q')
        (child / 'code').write_text('upstream\n')
        git(child, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(vendor), 'external/vendor')
        git(child, 'add', '.')
        git(child, 'commit', '-qm', 'base')
        base, base_tree = git(child, 'rev-parse', 'HEAD'), git(child, 'write-tree')
        (child / 'code').write_text('patched\n')
        git(child, 'add', '.')
        git(child, 'commit', '-qm', 'patch')
        fork, tree = git(child, 'rev-parse', 'HEAD'), git(child, 'write-tree')
        path = source / 'patches/dxvk-nvapi/0001-change.patch'
        path.parent.mkdir(parents=True)
        path.write_bytes(subprocess.check_output(['git', '-C', str(child), 'format-patch', '-1', '--stdout', '--no-signature']))
        git(child, 'reset', '--hard', base)
        git(source, 'add', '.')
        git(source, 'commit', '-qm', 'tool')
        spec = {'upstream_tree': base_tree, 'fork_commit': fork, 'fork_tree': tree,
                'prepared_tree': tree, 'patches': [{'path': path.relative_to(source).as_posix(),
                                                  'sha256': sha256(path), 'fork_change': True}]}
        lock = {'components': {'dxvk-nvapi': base}, 'component_patches': {'dxvk-nvapi': spec}}
        return source, child, lock

    def test_private_preparation_preserves_upstream_and_archives_expanded_patched_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, original, lock = self.fixture(root)
            # Untracked files must not enter the compiled source snapshot.
            (original / 'untracked.h').write_text('must not copy\n')
            prepared = root / 'build/dxvk-nvapi'
            record = patches.prepare(source, prepared, 'dxvk-nvapi', lock)
            self.assertEqual((original / 'code').read_text(), 'upstream\n')
            self.assertEqual(git(original, 'status', '--porcelain', '--untracked-files=no'), '')
            self.assertEqual(git(original, 'rev-parse', 'HEAD'), lock['components']['dxvk-nvapi'])
            self.assertEqual((prepared / 'code').read_text(), 'patched\n')
            self.assertFalse((prepared / 'untracked.h').exists())
            self.assertEqual((prepared / 'external/vendor/header.h').read_text(), 'vendor pin\n')
            self.assertEqual(git(prepared / 'external/vendor', 'rev-parse', '--show-toplevel'),
                             str(prepared / 'external/vendor'))
            dest = root / 'tool'
            provenance = patches.stage_sources(source, prepared, 'dxvk-nvapi', dest, lock)
            self.assertEqual(provenance['prepared_tree'], record['prepared_tree'])
            self.assertEqual(provenance['submodules']['external/vendor'],
                             git(original / 'external/vendor', 'rev-parse', 'HEAD'))
            with tarfile.open(dest / 'sources/dxvk-nvapi-prepared.tar.gz') as archive:
                self.assertEqual(archive.extractfile('code').read(), b'patched\n')
                self.assertEqual(archive.extractfile('external/vendor/header.h').read(), b'vendor pin\n')
                self.assertNotIn('untracked.h', archive.getnames())
            with self.assertRaisesRegex(ValueError, 'must be new'):
                patches.prepare(source, prepared, 'dxvk-nvapi', lock)
            (prepared / 'code').write_text('tampered after build\n')
            with self.assertRaises(subprocess.CalledProcessError):
                patches.stage_sources(source, prepared, 'dxvk-nvapi', root / 'bad-tool', lock)

    def test_wrong_pin_dirty_tree_nested_pin_and_patch_failure_are_fatal(self):
        for mode in ['wrong-pin', 'dirty', 'nested', 'patch-failure', 'wrong-result']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, original, lock = self.fixture(root)
                if mode == 'wrong-pin':
                    lock['components']['dxvk-nvapi'] = '0' * 40
                elif mode == 'dirty':
                    (original / 'code').write_text('dirty\n')
                elif mode == 'nested':
                    (original / 'external/vendor/header.h').write_text('dirty nested\n')
                elif mode == 'patch-failure':
                    path = source / lock['component_patches']['dxvk-nvapi']['patches'][0]['path']
                    path.write_text(path.read_text().replace('-upstream', '-unknown base'))
                    lock['component_patches']['dxvk-nvapi']['patches'][0]['sha256'] = sha256(path)
                else:
                    lock['component_patches']['dxvk-nvapi']['prepared_tree'] = '0' * 40
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    patches.prepare(source, root / 'build/dxvk-nvapi', 'dxvk-nvapi', lock)


if __name__ == '__main__':
    unittest.main()
