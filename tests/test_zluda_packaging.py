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
import wine_deps


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


class BootstrapFixtures(unittest.TestCase):
    @unittest.skipUnless(shutil.which('dpkg-deb'), 'dpkg-deb is required for the extraction fixture')
    def test_sdk_extracts_real_small_debs_with_absent_parents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / 'cache'
            cache.mkdir()
            downloads, runtime = [], {}
            for name in ['first', 'second']:
                package = root / ('input-' + name)
                (package / 'DEBIAN').mkdir(parents=True)
                (package / 'DEBIAN/control').write_text(
                    f'Package: fixture-{name}\nVersion: 1\nArchitecture: all\n'
                    'Maintainer: Fixture <fixture@example.invalid>\nDescription: tiny extraction fixture\n')
                library = f'lib{name}.so.1.0'
                payload = package / 'opt/rocm-7.2.4/lib' / library
                payload.parent.mkdir(parents=True)
                payload.write_bytes(b'\x7fELF' + name.encode())
                notice = package / 'usr/share/doc' / name / 'copyright'
                notice.parent.mkdir(parents=True)
                notice.write_text('fixture license')
                archive = cache / (name + '.deb')
                subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(archive)],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                downloads.append({'id': name, 'group': 'rocm', 'url': 'https://fixture.invalid/' + archive.name,
                                  'sha256': inputs.sha256(archive), 'usage': 'runtime', 'runtime_files': [library]})
                runtime[library] = [f'lib{name}.so.1', f'lib{name}.so']
            env = {'BUILD_ROOT': str(root / 'missing/build'), 'DOWNLOAD_ROOT': str(cache),
                   'DESTDIR': str(root / 'missing/dest'), 'SDK_ROOT': str(root / 'missing/sdk/opt/rocm-7.2.4')}
            with mock.patch.dict(os.environ, env), mock.patch.object(
                    overlay, 'LOCK', {'downloads': downloads, 'rocm_runtime': runtime}):
                overlay.sdk()
                for library, aliases in runtime.items():
                    installed = Path(env['DESTDIR']) / 'files/lib64' / library
                    self.assertEqual(installed.read_bytes(),
                                     (Path(env['SDK_ROOT']) / 'lib' / library).read_bytes())
                    for alias in aliases:
                        self.assertEqual(os.readlink(installed.parent / alias), library)
                for name in ['first', 'second']:
                    self.assertTrue((Path(env['BUILD_ROOT']) / 'packages' / name).is_dir())
                    self.assertEqual((Path(env['DESTDIR']) / 'licenses/rocm' / name /
                                      'usr/share/doc' / name / 'copyright').read_text(), 'fixture license')
                with self.assertRaisesRegex(ValueError, 'must be new'):
                    overlay.sdk()

    @unittest.skipUnless(shutil.which('dpkg-deb'), 'dpkg-deb is required for the extraction fixture')
    def test_build_only_missing_notices_allowed_and_runtime_missing_notices_refused(self):
        for usage in ['build', 'runtime']:
            with self.subTest(usage=usage), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = root / 'input'
                (package / 'DEBIAN').mkdir(parents=True)
                (package / 'DEBIAN/control').write_text(
                    'Package: fixture\nVersion: 1\nArchitecture: all\n'
                    'Maintainer: Fixture <fixture@example.invalid>\nDescription: no notice fixture\n')
                library = 'libfixture.so.1'
                file = package / 'opt/rocm-7.2.4' / ('include/hip/header.h' if usage == 'build' else 'lib/' + library)
                file.parent.mkdir(parents=True)
                file.write_bytes(b'\x7fELFfixture')
                cache = root / 'cache'
                cache.mkdir()
                archive = cache / 'fixture.deb'
                subprocess.run(['dpkg-deb', '--build', '--root-owner-group', str(package), str(archive)],
                               check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                item = {'id': 'fixture', 'group': 'rocm', 'url': 'https://fixture.invalid/fixture.deb',
                        'sha256': inputs.sha256(archive), 'usage': usage,
                        'runtime_files': [] if usage == 'build' else [library]}
                lock = {'downloads': [item], 'rocm_runtime': {} if usage == 'build' else {library: []}}
                env = {name: str(root / path) for name, path in
                       [('BUILD_ROOT', 'build'), ('DOWNLOAD_ROOT', 'cache'), ('DESTDIR', 'dest'), ('SDK_ROOT', 'sdk')]}
                with mock.patch.dict(os.environ, env), mock.patch.object(overlay, 'LOCK', lock):
                    if usage == 'runtime':
                        with self.assertRaisesRegex(ValueError, 'no license/copyright notices'):
                            overlay.sdk()
                    else:
                        overlay.sdk()
                        record = json.loads((root / 'dest/provenance/ROCM-PACKAGES.json').read_text())[0]
                        self.assertEqual(record['usage'], 'build')
                        self.assertEqual(record['sha256'], item['sha256'])
                        self.assertEqual(record['notices'], [])

    def test_wine_preflight_uses_pins_without_release_tags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'source'
            repo.mkdir()
            def run(*args):
                return subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                                                '-c', 'user.email=fixture@example.invalid', *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            run('init', '-q')
            (repo / 'source').write_text('GE baseline')
            run('add', '.')
            run('commit', '-qm', 'baseline')
            baseline = run('rev-parse', 'HEAD')
            (repo / 'source').write_text('overlay source')
            run('commit', '-qam', 'overlay')
            head = run('rev-parse', 'HEAD')
            self.assertEqual(run('tag', '--list'), '')
            lock = copy.deepcopy(inputs.LOCK)
            lock['ge_source'] = baseline
            helpers = root / 'helpers'
            helpers.mkdir()
            shutil.copyfile(inputs.HERE / 'inputs.py', helpers / 'inputs.py')
            (helpers / 'inputs.lock.json').write_text(json.dumps(lock))
            cache = root / 'downloads'
            cache.mkdir()
            archive = next(x for x in lock['downloads'] if x['id'] == 'ge')['url'].rsplit('/', 1)[1]
            (cache / archive).write_bytes(b'archive presence fixture')
            out = root / 'missing/wine'
            recipe = (inputs.HERE / 'build-wine.sh').read_text()
            # Execute the actual shell preflight, before any Wine/compiler work.
            preflight = recipe[recipe.index('rev=$(git -C "$repo" rev-parse --verify'):recipe.index('\nstamped()')]
            script = 'set -euo pipefail\n' + preflight + '\nprintf "%s\\n" "$rev" "$base" "$tgz"\n'
            env = dict(os.environ, repo=str(repo), here=str(helpers), cache=str(cache), out=str(out), rev='HEAD')
            for tags in ['absent', 'misleading']:
                if tags == 'misleading':
                    run('tag', 'GE-Proton99-99')
                result = subprocess.run(['bash', '-c', script], env=env, check=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.assertEqual(result.stdout.splitlines(), [head, baseline, str(cache / archive)])
                self.assertTrue((out / 'src').is_dir())
                self.assertTrue((out / 'stamps').is_dir())
            # A missing locked source commit must fail, never select a tag instead.
            lock['ge_source'] = '0' * 40
            (helpers / 'inputs.lock.json').write_text(json.dumps(lock))
            result = subprocess.run(['bash', '-c', script], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertNotEqual(result.returncode, 0)


class WinePrerequisites(unittest.TestCase):
    def test_recursive_build_tools_cap_jobs_and_preserve_jobserver(self):
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            fake = build / 'fake-tool'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o755)
            wine_deps.write_tools(build, fake, fake, 3)
            for name in ['make', 'ninja']:
                subprocess.run(['bash', '-n', str(build / 'proton-tools' / name)], check=True)
                result = subprocess.check_output([str(build / 'proton-tools' / name), 'target'],
                                                 env=dict(os.environ, MAKEFLAGS=''), text=True)
                self.assertEqual(result.splitlines(), ['-j3', 'target'])
            result = subprocess.check_output([str(build / 'proton-tools/make'), 'target'],
                                             env=dict(os.environ, MAKEFLAGS=' -j --jobserver-auth=3,4'), text=True)
            self.assertEqual(result.splitlines(), ['target'])
            with self.assertRaisesRegex(ValueError, '1..3'):
                wine_deps.write_tools(build, fake, fake, 4)

    def test_dependency_policy_and_recursive_source_pins_match_makefile(self):
        wine_deps.validate((ROOT / 'Makefile.in').read_text())
        for path in wine_deps.POLICY['sources'].values():
            self.assertIn(path, inputs.components_for_part('bridges'))
            self.assertEqual(inputs.LOCK['components'][path], inputs.git(ROOT, 'rev-parse', 'HEAD:' + path))
        text = (ROOT / 'Makefile.in').read_text().replace('WINE_DEPENDS = ffmpeg', 'WINE_DEPENDS = another ffmpeg')
        with self.assertRaisesRegex(ValueError, 'prerequisite policy differs'):
            wine_deps.validate(text)

    def test_generated_makefile_preserves_ge_flags_and_recipes(self):
        original = (ROOT / 'Makefile.in').read_text()
        with tempfile.TemporaryDirectory() as temporary:
            generated = wine_deps.generate(ROOT, Path(temporary) / 'wine')
            text = generated.read_text()
            for name in ['WINE_AUTOCONF_ARGS', 'WINE_i386_AUTOCONF_ARGS', 'WINE_x86_64_AUTOCONF_ARGS']:
                self.assertEqual(wine_deps.assignments(text)[name], wine_deps.assignments(original)[name])
            self.assertIn('--enable-werror', text)
            self.assertIn('--with-wayland', text)
            self.assertIn('--with-ffmpeg', text)
            self.assertNotIn('$(call rules-source,wine,', text)
            self.assertNotIn('$(call rules-wine-requests,wine)', text)
            self.assertIn(f'override WINE_ORIGIN := {generated.parent / "src/wine"}', text)
            self.assertNotIn('$(call rules-source,dxvk,', text)
            for name in wine_deps.POLICY['sources']:
                self.assertIn('$(call rules-source,' + name + ',', text)
            self.assertIn('$(call rules-meson,wayland-protocols,x86_64,unix)', text)
            self.assertIn('$(call rules-meson,wayland-protocols,i386,unix)', text)
            self.assertIn('$(WAYLAND_PROTOCOLS_x86_64_DST)/share/pkgconfig', text)
            self.assertIn('$(WAYLAND_PROTOCOLS_i386_DST)/share/pkgconfig', text)

    @unittest.skipUnless(shutil.which('make') or os.environ.get('PACKAGING_TEST_MAKE'), 'GNU Make is required for environment inspection')
    def test_make_expands_native_and_32bit_dependency_environments(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = wine_deps.generate(ROOT, Path(temporary) / 'wine')
            with generated.open('a') as out:
                for arch in ['x86_64', 'i386']:
                    out.write(f'$(info INSPECT_{arch}=$(WINE_{arch}_ENV) $(WINE_AUTOCONF_ARGS) $(WINE_{arch}_AUTOCONF_ARGS))\n')
            # Disable every shell-side parse action and source syncing: this
            # inspects expanded make variables only, never a compiler or build.
            result = subprocess.run([os.environ.get('PACKAGING_TEST_MAKE') or shutil.which('make'),
                                     '-C', str(generated.parent / 'proton-deps'), '-f', str(generated),
                                     '-n', 'overlay-inspect', 'rules-source=', 'HOST_ARCH=x86_64', 'SHELL=/bin/true'],
                                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for arch, triplet in [('x86_64', 'x86_64-linux-gnu'), ('i386', 'i386-linux-gnu')]:
                line = next(line for line in result.stdout.splitlines() if line.startswith('INSPECT_' + arch + '='))
                self.assertIn('dst-libxkbcommon-' + arch + '/include', line)
                self.assertIn('dst-wayland-protocols-' + arch + '/share/pkgconfig', line)
                self.assertIn('/usr/lib/' + triplet + '/pkgconfig', line)
                self.assertIn('--with-wayland', line)
                self.assertIn('--with-ffmpeg', line)
                self.assertIn('--with-mingw=gcc', line)


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
