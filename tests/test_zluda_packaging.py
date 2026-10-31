import copy
import json
import os
import re
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
import wine_scope
import native_audit
import native_plan
import wine_targets
import wine_sdk
import bridge_tools
import cuda_package
sys.path.insert(0, str(ROOT / "tests/fixtures"))
from cuda_binary import pe, elf




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
    @unittest.skipUnless(os.environ.get('PACKAGING_TEST_MAKE') or shutil.which('make'), 'GNU Make is required for the bridge graph fixture')
    def test_make_bridge_runs_after_wine_and_uses_relocated_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text = ('TARGET_ARCH := x86_64\nOBJ := ' + str(root) + '\nSRC := ' + str(ROOT) + '\n'
                    'SRCDIR := ' + str(root / 'source') + '\nDST_LIBDIR := ' + str(root / 'dist/files/lib') + '\n'
                    'DST_BASE := ' + str(root / 'dist') + '\n'
                    'WINE_SRC := prepared-source\nWINE_x86_64_OBJ := prepared-object\n'
                    'define rules-source\nZLUDA_BRIDGE_SRC := $(2)\nendef\n'
                    'include ' + str(ROOT / 'make/zluda-wine-bridge.mk') + '\n')
            for stamp in ['zluda-bridge-post-source', 'wine-x86_64-build', 'wine-x86_64-dist', 'wine-i386-dist']:
                text += str(root / ('.' + stamp)) + ':\n\t@echo gate-' + stamp + '\n'
            path = root / 'Makefile'
            path.write_text(text)
            result = subprocess.check_output([os.environ.get('PACKAGING_TEST_MAKE') or 'make', '--no-print-directory', '-n', '-f', str(path), 'all-dist'],
                                             cwd=root, text=True)
            self.assertIn(str(root / 'source/zluda/wine/zluda_wine'), result)
            self.assertIn('--generated', result)
            self.assertNotIn('build-wine64.txt', result)
            self.assertIn('--object "prepared-object" --source "prepared-source"', result)
            install = result.index('cp -a')
            self.assertLess(result.index('gate-wine-x86_64-dist'), install)
            self.assertLess(result.index('gate-wine-i386-dist'), install)
            self.assertIn('JOBS=3 bash', result)

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
        for name in ['files/lib/wine-cuda/zluda/i386-windows/nvcuda.dll',
                     'files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so',
                     'files/lib/wine/nvapi/i386-windows/nvapi.dll', 'proton_zluda.py']:
            self.assertIn(name, required)

    def test_launcher_loader_order_is_preserved(self):
        import proton_zluda
        from types import SimpleNamespace
        env = {'PROTON_ENABLE_ZLUDA': '1', 'LD_LIBRARY_PATH': '/base'}
        with mock.patch.object(proton_zluda, 'select_root'), mock.patch.object(proton_zluda, 'launch_warnings'):
            proton_zluda.configure_launch(SimpleNamespace(env=env, log_file=None),
                SimpleNamespace(base_dir='/tool', host_pe_arch='x86_64-windows'), [], 'LD_LIBRARY_PATH', lambda message: None)
        self.assertEqual(env['LD_LIBRARY_PATH'], '/tool/files/lib64/wine/x86_64-unix:/tool/files/lib64:/base')



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
    @staticmethod
    def make_edges(makefile):
        result = subprocess.run([os.environ.get('PACKAGING_TEST_MAKE') or shutil.which('make'),
                                 '-C', str(makefile.parent / 'proton-deps'), '-f', str(makefile),
                                 '-np', 'overlay-inspect', 'rules-source=',
                                 'HOST_ARCH=x86_64', 'SHELL=/bin/true'], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        graph = {}
        for line in result.stdout.splitlines():
            if not line or line[0] in '#\t ' or ':' not in line:
                continue
            target, dependencies = line.split(':', 1)
            if re.match(r'\s*\w+\s*[:+?]?=', dependencies):
                continue
            graph[target] = set(dependencies.split()) - {'|'}
        return graph

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
        for key in ['OPENFST_DEPENDS', 'KALDI_i386_DEPENDS', 'PIPER_x86_64_DEPENDS']:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                wine_deps.validate((ROOT / 'Makefile.in').read_text() + f'\n{key} = missing\n')

    def test_component_preflight_keeps_destdir_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = (inputs.HERE / 'build-components.sh').read_text()
            preflight = recipe[recipe.index('[[ ! -e $DESTDIR ]]'):recipe.index('\nif [[ $part == zluda')]
            script = ('set -euo pipefail\n' + preflight +
                      '\n[[ -d $DESTDIR ]]\n'
                      'python3 -c \'import os; assert "DESTDIR" not in os.environ\'\n'
                      'DESTDIR=$DESTDIR python3 -c \'import os; assert os.path.isdir(os.environ["DESTDIR"])\'\n')
            subprocess.run(['bash', '-c', script], check=True,
                           env=dict(os.environ, DESTDIR=str(root / 'overlay'),
                                    BUILD_ROOT=str(root / 'build'), DOWNLOAD_ROOT=str(root / 'downloads'),
                                    SOURCE_ROOT=str(ROOT)))

    @unittest.skipUnless(shutil.which('make') or os.environ.get('PACKAGING_TEST_MAKE'), 'GNU Make is required')
    def test_ge_install_uses_private_prefix_with_exported_overlay_destdir(self):
        # Run GE's actual configure/build/install rules with a tiny fake library,
        # not a compiler. This caught the run-3 double-staging of OpenFST headers.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = wine_deps.generate(ROOT, root / 'wine')
            deps = generated.parent / 'proton-deps'
            source = root / 'openfst'
            source.mkdir()
            (source / 'configure').write_text(
                '#!/bin/sh\nset -eu\nfor arg do case "$arg" in --prefix=*) prefix=${arg#*=};; esac; done\n'
                'printf "prefix := %s\\n" "$prefix" > Makefile\n'
                "cat >> Makefile <<'EOF'\noverride SHELL := /bin/sh\n.PHONY: all install\nall:\n\t@:\ninstall:\n"
                '\tmkdir -p "$(DESTDIR)$(prefix)/include/fst"\n'
                '\tprintf fixture > "$(DESTDIR)$(prefix)/include/fst/types.h"\nEOF\n')
            (source / 'configure').chmod(0o755)
            (deps / 'obj-openfst-x86_64').mkdir()
            for stamp in ['.openfst-source', '.openfst-post-source']:
                (deps / stamp).touch()
            target = str(deps / '.openfst-x86_64-post-build')
            make = os.environ.get('PACKAGING_TEST_MAKE') or shutil.which('make')
            command = [make, '-C', str(deps), '-f', str(generated), target, 'rules-source=',
                       'HOST_ARCH=x86_64', 'SHELL=/bin/true', f'MAKE={make}',
                       f'OPENFST_SRC={source}', f'OPENFST_ORIGIN={source}']
            # Parse-time shell calls stay disabled; enable only these fixture
            # recipes and their inherited prerequisite/recursive commands.
            with generated.open('a') as out:
                out.write(f'{target}: override SHELL = /bin/sh\n')
            subprocess.run(command, check=True,
                           env=dict(os.environ, DESTDIR=str(root / 'wrong-env-stage')),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual((deps / 'dst-openfst-x86_64/include/fst/types.h').read_text(), 'fixture')
            self.assertFalse((root / 'wrong-env-stage').exists())

    @unittest.skipUnless(shutil.which('make') or os.environ.get('PACKAGING_TEST_MAKE'), 'GNU Make is required')
    def test_focused_make_preserves_full_ge_dependency_edge_sets(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = wine_deps.generate(ROOT, Path(temporary) / 'wine')
            focused = generated.read_text()
            # Independent reference: instantiate ALL GE component factories.
            # Only unrelated external includes are omitted. Neither source
            # syncing nor parse-time shell calls execute during database reads.
            original = (ROOT / 'Makefile.in').read_text().splitlines()
            original = [line for line in original if not line.startswith('include ') or
                        line.startswith('include $(SRC)/make/')]
            original = [line + ' wayland-protocols' if line.startswith('WINE_DEPENDS =') else line
                        for line in original]
            for arch in ['i386', 'x86_64']:
                original.append(f'$(eval $(call rules-meson,wayland-protocols,{arch},unix))')
            reference = generated.parent / 'reference.mk'
            reference.write_text(focused.split('# Enable secondary expansions,')[0] +
                                 '\n'.join(original) + '\noverlay-inspect:\n\t@:\n')
            expected, actual = self.make_edges(reference), self.make_edges(generated)
            for name in [*wine_deps.POLICY['sources'], 'wine']:
                for arch in ['i386', 'x86_64']:
                    targets = [str(generated.parent / 'proton-deps' / f'.{name}-{arch}-{stage}')
                               for stage in ['configure', 'build', 'post-build']]
                    targets += [f'{name}-{arch}-{stage}' for stage in ['configure', 'build']]
                    for target in targets:
                        if target not in expected:
                            continue  # GE intentionally has no i386 Piper/OpenXR.
                        # Compare dependency edges, including the source and
                        # cross-architecture Kaldi serialization. Wine's extra
                        # generated-Makefile invalidation is intentionally local.
                        # Request headers were already generated in the patched
                        # Wine scratch tree; its factory is deliberately omitted.
                        prepared = {str(generated.parent / 'src/wine' / x) for x in
                                    ['include/wine/server_protocol.h', 'server/request_handlers.h',
                                     'server/request_trace.h']}
                        prepared.add(str(generated.parent / 'proton-deps/.wine-wine-requests'))
                        wanted = {x for x in expected[target] if not x.endswith('.mk')}
                        wanted -= prepared
                        got = {x for x in actual[target] if not x.endswith('.mk')}
                        with self.subTest(target=target):
                            self.assertEqual(got, wanted)

    @unittest.skipUnless(shutil.which('make') or os.environ.get('PACKAGING_TEST_MAKE'), 'GNU Make is required')
    def test_native_profile_has_no_reachable_i386_prerequisites(self):
        with tempfile.TemporaryDirectory() as temporary:
            generated = wine_deps.generate(ROOT, Path(temporary) / 'wine', native_only=True)
            graph = self.make_edges(generated)
            visited, pending = set(), ['overlay-wine-x86_64']
            while pending:
                target = pending.pop()
                if target in visited:
                    continue
                visited.add(target)
                pending.extend(graph.get(target, set()))
            self.assertFalse([x for x in visited if '-i386' in x], sorted(visited))
            for component in wine_deps.POLICY['wine']['common'] + wine_deps.POLICY['wine']['x86_64']:
                self.assertIn(str(generated.parent / 'proton-deps' / f'.{component}-x86_64-build'), visited)
            self.assertEqual(wine_deps.assignments(generated.read_text())['WINE_x86_64_AUTOCONF_ARGS'],
                             wine_deps.assignments((ROOT / 'Makefile.in').read_text())['WINE_x86_64_AUTOCONF_ARGS'])

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
                out.write('$(info INSPECT_BASE=$(BASE_SOURCE_DATE_EPOCH))\n')
                for arch in ['x86_64', 'i386']:
                    out.write(f'$(info INSPECT_{arch}=$(WINE_{arch}_ENV) $(WINE_AUTOCONF_ARGS) $(WINE_{arch}_AUTOCONF_ARGS))\n')
            # Disable every shell-side parse action and source syncing: this
            # inspects expanded make variables only, never a compiler or build.
            result = subprocess.run([os.environ.get('PACKAGING_TEST_MAKE') or shutil.which('make'),
                                     '-C', str(generated.parent / 'proton-deps'), '-f', str(generated),
                                     '-n', 'overlay-inspect', 'rules-source=', 'HOST_ARCH=x86_64', 'SHELL=/bin/true'],
                                    env=dict(os.environ, SOURCE_DATE_EPOCH='1793260800'),
                                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertIn('INSPECT_BASE=1793260800', result.stdout.splitlines())
            for arch, triplet in [('x86_64', 'x86_64-linux-gnu'), ('i386', 'i386-linux-gnu')]:
                line = next(line for line in result.stdout.splitlines() if line.startswith('INSPECT_' + arch + '='))
                self.assertIn('dst-libxkbcommon-' + arch + '/include', line)
                self.assertIn('dst-wayland-protocols-' + arch + '/share/pkgconfig', line)
                self.assertIn('/usr/lib/' + triplet + '/pkgconfig', line)
                self.assertIn('--with-wayland', line)
                self.assertIn('--with-ffmpeg', line)
                self.assertIn('--with-mingw=gcc', line)


class WineTargets(unittest.TestCase):
    def fixture(self, arch):
        return (ROOT / 'tests/fixtures' / ('wine-' + arch + '.mk')).read_text()

    def test_fixture_provenance_and_configured_source_names(self):
        provenance = json.loads((ROOT / 'tests/fixtures/wine-targets-source.json').read_text())
        self.assertEqual(provenance['wine'], inputs.LOCK['components']['wine'])
        # CI has the recursive Wine source checkout. The host fixtures can also
        # run without populating that large submodule; their generator reads the
        # exact pinned Git objects and records every input hash.
        for name, expected in provenance['sha256'].items():
            source = ROOT / 'wine' / name
            if source.is_file():
                self.assertEqual(inputs.sha256(source), expected, name)
        for arch in ['x86_64', 'i386']:
            targets = wine_targets.declared_targets(self.fixture(arch))
            self.assertNotIn('dlls/winecrt0/all', targets)
            self.assertIn(f'dlls/winecrt0/{arch}-windows/libwinecrt0.a', targets)

    def test_both_architectures_native_only_and_complete_build_profiles(self):
        for arch in ['x86_64', 'i386']:
            text = self.fixture(arch)
            targets = wine_targets.select(text, arch, 'both', ['dlls/ntdll', 'server', 'programs/wineboot'])
            self.assertIn('include/all', targets)
            self.assertIn('dlls/ntdll/all', targets)
            self.assertIn('server/all', targets)
            self.assertNotIn('dlls/winecrt0/all', targets)
            if arch == 'x86_64':
                self.assertIn('dlls/ntdll/ntdll.so', targets)
                for name in ['winecrt0', 'kernel32', 'ntdll']:
                    self.assertIn(f'dlls/{name}/x86_64-windows/lib{name}.a', targets)
                    self.assertIn(f'dlls/{name}/lib{name}.a', targets)
            else:
                self.assertFalse(any(target.startswith('tools/') for target in targets))
            all_targets = wine_targets.select(text, arch, 'all', [])
            self.assertIn('all', all_targets)
            if arch == 'x86_64':
                self.assertIn('dlls/ntdll/ntdll.so', all_targets)
            self.assertTrue(set(all_targets) <= wine_targets.declared_targets(text))
        targets = wine_targets.select(self.fixture('x86_64'), 'x86_64', 'x86_64-unix-ntdll', ['dlls/ntdll'])
        self.assertIn('dlls/ntdll/ntdll.so', targets)
        self.assertNotIn('dlls/ntdll/all', targets)
        self.assertFalse(any(target.endswith('.dll') for target in targets))
        with self.assertRaisesRegex(ValueError, 'cannot build i386'):
            wine_targets.select(self.fixture('i386'), 'i386', 'x86_64-unix-ntdll', ['dlls/ntdll'])
        missing = self.fixture('x86_64').replace('dlls/winecrt0/x86_64-windows/libwinecrt0.a:', '')
        with self.assertRaisesRegex(ValueError, 'SDK archive target: dlls/winecrt0'):
            wine_targets.select(missing, 'x86_64', 'both', ['dlls/ntdll'])
        missing_native = self.fixture('x86_64').replace('dlls/kernel32/libkernel32.a:', '')
        with self.assertRaisesRegex(ValueError, 'no declared target: dlls/kernel32/libkernel32.a'):
            wine_targets.select(missing_native, 'x86_64', 'x86_64-unix-ntdll', ['dlls/ntdll'])

    @unittest.skipUnless(shutil.which('make') or os.environ.get('PACKAGING_TEST_MAKE'), 'GNU Make is required')
    def test_gnu_make_accepts_discovered_goals_and_rejects_old_crt_aggregate(self):
        make = os.environ.get('PACKAGING_TEST_MAKE') or shutil.which('make')
        for arch, profile in [('x86_64', 'both'), ('i386', 'both'), ('x86_64', 'x86_64-unix-ntdll'),
                              ('x86_64', 'all'), ('i386', 'all')]:
            fixture = ROOT / 'tests/fixtures' / ('wine-' + arch + '.mk')
            targets = wine_targets.select(fixture.read_text(), arch, profile, ['dlls/ntdll'])
            subprocess.run([make, '-n', '-f', str(fixture), *targets], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        result = subprocess.run([make, '-n', '-f', str(fixture), 'dlls/winecrt0/all'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No rule to make target', result.stderr)

    def test_cli_reads_configured_file_records_plan_and_executes_those_targets(self):
        import gzip
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'Makefile').write_text(self.fixture('x86_64'))
            (root / 'modules').write_text('dlls/ntdll\n')
            make = root / 'make'
            make.write_text('#!/usr/bin/env python3\nimport json, pathlib, sys\n'
                            'pathlib.Path("called.json").write_text(json.dumps(sys.argv[1:]))\n')
            make.chmod(0o755)
            subprocess.run([sys.executable, str(inputs.HERE / 'wine_targets.py'), 'Makefile',
                            '--arch', 'x86_64', '--profile', 'x86_64-unix-ntdll', '--modules', 'modules',
                            '--make-tool', str(make), '--report', 'log/targets.json'], cwd=root, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            record = json.loads((root / 'log/targets.json').read_text())
            self.assertEqual(record['targets'], json.loads((root / 'called.json').read_text()))
            self.assertEqual(record['makefile_sha256'], inputs.sha256(root / 'Makefile'))
            self.assertEqual(gzip.decompress((root / 'log/targets.Makefile.gz').read_bytes()),
                             (root / 'Makefile').read_bytes())
            self.assertIn('dlls/ntdll/ntdll.so', record['targets'])


class BridgeSDK(unittest.TestCase):
    def source_fixture(self, root):
        fixture = json.loads((ROOT / 'tests/fixtures/bridge-cross-files.json').read_text())
        for component, record in fixture.items():
            self.assertEqual(record['pin'], inputs.LOCK['components'][component])
            (root / component).mkdir(parents=True)
            for name, file in record['files'].items():
                path = root / component / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(file['text'])
                self.assertEqual(inputs.sha256(path), file['sha256'])
                if (ROOT / component / name).is_file():
                    self.assertEqual(inputs.sha256(ROOT / component / name), file['sha256'])
        return root






    def test_generated_tool_preflight_has_no_retired_winelib_source_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source_fixture(root / 'source')
            groups = bridge_tools.commands(source, root / 'sdk', generated_bridge=True)
            commands = [command[0] for values in groups.values() for command in values]
            self.assertIn('rustc', commands)
            self.assertIn('cargo', commands)
            for arch in ['x86_64', 'i686']:
                for suffix in ['gcc', 'dlltool', 'objcopy']:
                    self.assertIn(arch + '-w64-mingw32-' + suffix, commands)
            self.assertIn(str(root / 'sdk/bin/winebuild'), commands)
            self.assertFalse(any('winegcc' in command for command in commands))

    def test_every_cross_compiler_and_tool_gets_version_preflight(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source_fixture(root / 'source')
            sdk = root / 'sdk'
            (sdk / 'bin').mkdir(parents=True)
            (sdk / 'bin/winebuild').write_text('#!/bin/sh\nexit 0\n')
            (sdk / 'bin/winebuild').chmod(0o755)
            tools = root / 'bin'
            tools.mkdir()
            groups = bridge_tools.commands(source, sdk)
            commands = {cmd[0] for values in groups.values() for cmd in values}
            for bits in ['i686', 'x86_64']:
                for suffix in ['gcc', 'g++', 'ar', 'strip', 'windres']:
                    self.assertIn(bits + '-w64-mingw32-' + suffix, commands)
            for command in commands:
                if '/' in command:
                    continue
                path = tools / command
                path.write_text('#!/bin/sh\ntest "$1" = --version || exit 45\nprintf "fixture version\\n"\n')
                path.chmod(0o755)
            with mock.patch.dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], TRACE=str(root / 'trace')):
                report = root / 'logs/preflight.json'
                records = bridge_tools.preflight(source, report, sdk)
                self.assertTrue(all(record['command'][-1] == '--version' for record in records))
                self.assertEqual(json.loads(report.read_text()), records)
                missing = tools / 'i686-w64-mingw32-g++'
                missing.write_text('#!/bin/sh\nexit 46\n')
                with self.assertRaisesRegex(ValueError, 'tool --version failed: i686-w64-mingw32-g[+][+]'):
                    bridge_tools.preflight(source, report, sdk)
                self.assertEqual(json.loads(report.read_text())[-1]['returncode'], 46)
            script = (inputs.HERE / 'build-components.sh').read_text()
            self.assertLess(script.index('bridge-tools-sdk.json'), script.index('build-bridge.sh'))
            script = (inputs.HERE / 'build-bridge.sh').read_text()
            self.assertIn('bash "$BRIDGE_SOURCE/build-bridge.sh"', script)
            self.assertIn('cuda_package.py" install', script)
            self.assertNotIn('meson setup', script)
            wine = (inputs.HERE / 'build-wine.sh').read_text()
            self.assertLess(wine.index('bridge-tools-system.json'), wine.index('build x86_64'))


class NativePlan(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / 'tests/fixtures/native-graph.json').read_text())

    def test_resolved_workspace_specs_and_features(self):
        fixture = self.fixture()
        self.assertEqual(fixture['origin']['pin'], '42b557a23c9c7868ee1fb736849451361a6e8893')
        self.assertEqual(fixture['origin']['Cargo.lock_sha256'], inputs.LOCK['cargo_lock_sha256'])
        record = native_plan.plan(fixture['metadata'], fixture['tree'])
        members = set(fixture['metadata']['workspace_members'])
        self.assertTrue(set(record['roots']) <= members)
        self.assertEqual(len(record['roots']), 4)
        self.assertIn('highs-sys/build', record['features'])
        self.assertIn('highs-sys/default', record['features'])
        self.assertFalse(any('hipfft' in f for f in record['features']))
        self.assertIn('--no-default-features', record['args'])
        for name in ['blake3', 'vergen-gix', 'highs']:
            self.assertIn('deferred', record['optional'][name])
        for name in ['lz4-sys', 'zstd-sys']:
            self.assertIn('transitively', record['optional'][name])
        self.assertIn('absent', record['optional']['zstd'])
        # A lockfile/resolved registry package is not an explicit workspace root.
        registry = next(p['id'] for p in fixture['metadata']['packages'] if p['name'] == 'blake3')
        for spec in ['blake3', registry, 'vergen-gix', 'lz4-sys', 'zstd']:
            with self.assertRaisesRegex(ValueError, 'unresolvable early package spec'):
                native_plan.validate_specs(record, record['args'] + ['-p', spec])

    def test_optional_absent_and_required_missing(self):
        fixture = self.fixture()
        lines = fixture['tree'].splitlines()
        tree = '\n'.join(line for line in lines if not line.startswith(('blake3 ', 'vergen-gix ', 'lz4-sys ', 'zstd-sys ')))
        record = native_plan.plan(fixture['metadata'], tree)
        for name in ['blake3', 'vergen-gix', 'lz4-sys', 'zstd-sys']:
            self.assertIn('absent', record['optional'][name])
        tree = '\n'.join(line for line in lines if not line.startswith('highs-sys '))
        with self.assertRaisesRegex(ValueError, 'required native graph packages missing: highs-sys'):
            native_plan.plan(fixture['metadata'], tree)

    def test_early_roots_cannot_start_llvm(self):
        fixture = self.fixture()
        ids = {p['name']: p['id'] for p in fixture['metadata']['packages']}
        for root in ['dark_api', 'highs-sys']:
            metadata = copy.deepcopy(fixture['metadata'])
            node = next(n for n in metadata['resolve']['nodes'] if n['id'] == ids[root])
            node['deps'].append({'pkg': ids['llvm_zluda'], 'dep_kinds': [{'kind': 'build'}]})
            if root == 'highs-sys':
                with self.assertRaisesRegex(ValueError, 'required native root would start LLVM'):
                    native_plan.plan(metadata, fixture['tree'])
            else:
                record = native_plan.plan(metadata, fixture['tree'])
                self.assertNotIn(ids[root], record['roots'])
                self.assertEqual(record['optional'][root], 'would start LLVM')

    def test_tree_features_duplicates_build_sections_and_bad_records(self):
        tree = 'highs-sys v1.11.0 (/source/ext/highs-sys)|build\n[build-dependencies]\nhighs-sys v1.11.0 (/source/ext/highs-sys)|default,highs_release (*)\n'
        self.assertEqual(native_plan.tree_packages(tree),
                         {('highs-sys', '1.11.0'): {'build', 'default', 'highs_release'}})
        with self.assertRaisesRegex(ValueError, 'unexpected Cargo tree record'):
            native_plan.tree_packages('bad record')


class NativeBuild(unittest.TestCase):
    def fixture(self, root):
        target, sdk = root / 'source/zluda/target/release', root / 'sdk'
        lib = sdk / 'lib'
        lib.mkdir(parents=True)
        (lib / 'libamdhip64.so').write_bytes(b'\x7fELFfixture')
        for package in ['highs-sys', *native_audit.AMD_PACKAGES]:
            out = target / 'build' / (package + '-fixture')
            out.mkdir(parents=True)
            text = ''
            if package == 'highs-sys':
                (out / 'out/lib').mkdir(parents=True)
                (out / 'out/lib/libhighs.a').write_bytes(b'!<arch>\n')
                text = f'cargo:rustc-link-search=native={out / "out/lib"}\ncargo:rustc-link-lib=static=highs\n'
            elif package == 'hip_runtime-sys':
                text = 'cargo:rustc-link-search=native=/opt/rocm/lib/\ncargo:rustc-link-lib=dylib=amdhip64\n'
            (out / 'output').write_text(text)
        return target, sdk

    def test_native_library_layout_and_wrong_staging_are_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, sdk = self.fixture(root)
            legacy = root / 'absent-legacy'
            result = native_audit.audit(target, sdk, [sdk / 'lib'], legacy)
            self.assertIn('amdhip64', result['hip_runtime-sys'][0]['resolved'])
            fft_output = target / 'build/hipfft-sys-fixture/output'
            fft_output.write_text('cargo:rustc-link-lib=dylib=hipfft\n')
            with self.assertRaisesRegex(ValueError, 'unexpected hipfft linking'):
                native_audit.audit(target, sdk, [sdk / 'lib'], legacy)
            fft_output.write_text('')
            archive = target / 'build/highs-sys-fixture/out/lib/libhighs.a'
            wrong = root / 'overlay' / archive.relative_to(root)
            wrong.parent.mkdir(parents=True)
            archive.rename(wrong)
            with self.assertRaisesRegex(ValueError, 'HiGHS static archive absent'):
                native_audit.audit(target, sdk, [sdk / 'lib'], legacy)
            wrong.rename(archive)
            (sdk / 'lib/libamdhip64.so').unlink()
            with self.assertRaisesRegex(ValueError, 'unresolved native library: amdhip64'):
                native_audit.audit(target, sdk, [sdk / 'lib'], legacy)

    def test_native_cmake_changes_only_highs_and_drops_destdir(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source/zluda/ext'
            (source / 'HiGHS').mkdir(parents=True)
            (source / 'highs-sys').mkdir()
            fake = root / 'cmake'
            fake.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                            'print(json.dumps([sys.argv[1:], os.environ.get("DESTDIR")]))\n')
            fake.chmod(0o755)
            env = dict(os.environ, SOURCE_ROOT=str(root / 'source'), ZLUDA_REAL_CMAKE=str(fake),
                       DESTDIR=str(root / 'wrong-stage'))
            for args, extra in [([str(source / 'highs-sys/../HiGHS'), '-DCMAKE_INSTALL_LIBDIR=lib/x86_64-linux-gnu'],
                                 ['-DCMAKE_INSTALL_LIBDIR=lib']),
                                (['--build', '/build', '--target', 'install'], []),
                                ([str(source / 'llvm-project/llvm')], []), (['--version'], [])]:
                output = subprocess.check_output([str(inputs.HERE / 'native-cmake.py'), *args], env=env, text=True)
                argv, dest = json.loads(output)
                self.assertEqual(argv, args + extra)
                self.assertIsNone(dest)

    def test_native_failure_stops_before_llvm_and_zluda(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, sdk = self.fixture(root)
            tools = root / 'bin'
            tools.mkdir()
            cargo = tools / 'cargo'
            cargo.write_text('#!/usr/bin/env python3\nimport json, os, sys\n'
                             'with open(os.environ["TRACE"], "a") as out:\n'
                             ' out.write(json.dumps([sys.argv[1:], os.environ.get("DESTDIR")])+"\\n")\n'
                             'fixture=json.load(open(os.environ["GRAPH_FIXTURE"]))\n'
                             'if sys.argv[1] == "metadata": print(json.dumps(fixture["metadata"]))\n'
                             'if sys.argv[1] == "tree": print(fixture["tree"])\n'
                             'if sys.argv[1] == "build" and "--no-default-features" in sys.argv:\n'
                             ' specs=[sys.argv[i+1] for i, arg in enumerate(sys.argv) if arg == "-p"]\n'
                             ' assert set(specs) <= set(fixture["metadata"]["workspace_members"])\n'
                             ' if os.environ.get("FAIL_NATIVE") == "1": sys.exit(42)\n')
            cargo.chmod(0o755)
            cmake = tools / 'cmake'
            cmake.write_text('#!/bin/sh\nexit 0\n')
            cmake.chmod(0o755)
            rustc = tools / 'rustc'
            rustc.write_text('#!/bin/sh\nprintf "host: x86_64-unknown-linux-gnu\\n"\n')
            rustc.chmod(0o755)
            for fail in ['1', '0']:
                trace = root / ('trace-' + fail)
                env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'],
                           SOURCE_ROOT=str(root / 'source'), BUILD_ROOT=str(root / 'build'), SDK_ROOT=str(sdk),
                           LIBRARY_PATH=str(sdk / 'lib'), TRACE=str(trace), FAIL_NATIVE=fail, DESTDIR='/wrong-stage',
                           GRAPH_FIXTURE=str(ROOT / 'tests/fixtures/native-graph.json'))
                result = subprocess.run(['bash', str(inputs.HERE / 'build-native.sh')], env=env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                calls = [json.loads(line) for line in trace.read_text().splitlines()]
                self.assertEqual(result.returncode, 42 if fail == '1' else 0, result.stderr)
                self.assertTrue(all(dest is None for _, dest in calls))
                self.assertEqual(calls[0][0], ['fetch', '--locked'])
                self.assertEqual(calls[1][0][0], 'metadata')
                self.assertEqual(calls[2][0][0], 'tree')
                self.assertEqual(calls[1][0][calls[1][0].index('--filter-platform') + 1], native_plan.TARGET)
                self.assertEqual(calls[2][0][calls[2][0].index('--target') + 1], native_plan.TARGET)
                self.assertEqual(calls[2][0][calls[2][0].index('-p') + 1], 'zluda')
                self.assertIn('normal,build', calls[2][0])
                early = calls[3][0]
                self.assertTrue(any('highs-sys@' in arg for arg in early))
                self.assertTrue(any('dark_api@' in arg for arg in early))
                self.assertNotIn('blake3', early)
                self.assertNotIn('hipfft-sys', early)
                self.assertNotIn('--keep-going', early)
                if fail == '1':
                    self.assertEqual(len(calls), 4)
                else:
                    self.assertEqual([call[0][-1] for call in calls[4:]], ['llvm_zluda', 'zluda'])



class WineScope(unittest.TestCase):
    def test_only_exact_prepared_source_hunk_selects_native_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                                                '-c', 'user.email=fixture@example.invalid', *args],
                                               stderr=subprocess.DEVNULL).decode().strip()
            git('init', '-q')
            path = repo / wine_scope.SOURCE
            path.parent.mkdir(parents=True)
            before = 'prefix\n            RIP_sig(ucontext) = (intptr_t)steamclient_addr;\n            return;\nsuffix\n'
            after = before.replace('            return;', '            leave_handler( ucontext );\n            return;')
            path.write_text(before)
            git('add', '.')
            git('commit', '-qm', 'baseline')
            baseline = git('rev-parse', 'HEAD')
            for text, extra, mode in [(after, False, 'x86_64-unix-ntdll'),
                                      (after + 'another change\n', False, 'both'),
                                      (after, True, 'both'), (before + 'different fix\n', False, 'both')]:
                path.write_text(text)
                other = repo / 'other.c'
                if extra:
                    other.write_text('new ABI')
                else:
                    other.unlink(missing_ok=True)
                git('add', '-A')
                git('commit', '-qm', 'candidate')
                self.assertEqual(wine_scope.select(repo, baseline, 'HEAD')['mode'], mode)

    def test_archive_hashes_preserved_counterparts_and_rejects_mixing(self):
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, record = root / 'ge.tar.gz', root / 'scope.json'
            record.write_text(json.dumps({'mode': 'x86_64-unix-ntdll'}))
            with tarfile.open(archive, 'w:gz') as stream:
                for name in [*wine_scope.PRESERVED, wine_scope.OUTPUT,
                             'files/lib/wine/i386-windows/kernel32.dll', wine_scope.REPLACED_PE32]:
                    data = name.encode()
                    member = tarfile.TarInfo('GE/' + name)
                    member.size = len(data)
                    stream.addfile(member, io.BytesIO(data))
                    target = root / 'tool' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                link = 'files/lib/wine/i386-unix/fixture-link'
                member = tarfile.TarInfo('GE/' + link)
                member.type, member.linkname = tarfile.SYMTYPE, 'ntdll.so'
                stream.addfile(member)
                (root / 'tool' / link).symlink_to('ntdll.so')
            wine_scope.inventory(archive, root / 'names', root / 'sizes', record)
            scope = json.loads(record.read_text())
            self.assertIn('files/lib/wine/i386-windows/kernel32.dll', scope['ge_preserved_sha256'])
            self.assertNotIn(wine_scope.REPLACED_PE32, scope['ge_preserved_sha256'])
            wine_scope.verify(root / 'tool', scope)
            (root / 'tool' / wine_scope.PRESERVED[0]).write_bytes(b'rebuilt PE32')
            with self.assertRaisesRegex(ValueError, 'preserved GE Wine file differs'):
                wine_scope.verify(root / 'tool', scope)
            (root / 'tool' / wine_scope.PRESERVED[0]).write_bytes(wine_scope.PRESERVED[0].encode())
            (root / 'tool' / link).unlink()
            (root / 'tool' / link).symlink_to('different')
            with self.assertRaisesRegex(ValueError, 'preserved GE Wine link differs'):
                wine_scope.verify(root / 'tool', scope)
            with self.assertRaisesRegex(ValueError, 'missing preserved'):
                with tarfile.open(root / 'empty.tar.gz', 'w:gz'):
                    pass
                wine_scope.inventory(root / 'empty.tar.gz', root / 'names', root / 'sizes', record)


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
            pf_base = run(pf, 'rev-parse', 'HEAD')
            pf_base_tree = run(pf, 'write-tree')
            run(source, 'add', '.')
            run(source, 'commit', '-qm', 'base tool')
            base = run(source, 'rev-parse', 'HEAD')
            (pf / 'changed.py').write_text('new')
            (pf / 'deleted.py').unlink()
            run(pf, 'add', '-A')
            run(pf, 'commit', '-qm', 'new fixes')
            pf_fork = run(pf, 'rev-parse', 'HEAD')
            pf_tree = run(pf, 'write-tree')
            patch = source / 'patches/protonfixes/0001-fixture.patch'
            patch.parent.mkdir(parents=True)
            patch.write_bytes(subprocess.check_output(['git', '-C', str(pf), 'format-patch', '-1', '--stdout']))
            run(pf, 'reset', '--hard', pf_base)
            run(source, 'add', '.')
            run(source, 'commit', '-qm', 'new tool')
            commit = run(source, 'rev-parse', 'HEAD')
            lock = copy.deepcopy(inputs.LOCK)
            lock['components']['protonfixes'] = pf_base
            lock['component_patches'] = {'protonfixes': {
                'upstream_tree': pf_base_tree, 'fork_commit': pf_fork, 'fork_tree': pf_tree,
                'prepared_tree': pf_tree, 'patches': [{'path': patch.relative_to(source).as_posix(),
                    'sha256': inputs.sha256(patch), 'fork_change': True}]}}
            lock['ge_source'] = base
            ge = root / 'ge'
            ge.mkdir()
            tool = ge / 'GE-Proton11-7'
            (tool / 'protonfixes').mkdir(parents=True)
            stock = {}
            for name in wine_scope.PRESERVED:
                path = tool / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'stock GE ' + name.encode())
                stock[name] = inputs.sha256(path)
            stub_hashes = {}
            for arch, machine in [('x86_64-windows', 0x8664), ('i386-windows', 0x14c)]:
                path = tool / 'files/lib/wine' / arch / 'nvcuda.dll'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pe(machine))
                stub_hashes[arch] = inputs.sha256(path)
            for directory, arch in cuda_package.policy.ARCHES.items():
                path = tool / 'files/share/default_pfx/drive_c/windows' / directory / 'nvcuda.dll'
                path.parent.mkdir(parents=True)
                path.symlink_to('../../../../../lib/wine/' + arch + '/nvcuda.dll')
            (tool / 'protonfixes/deleted.py').write_text('old')
            legacy = tool / 'files/lib/wine/nvidia-libs/nvcuda/x86_64-windows/nvcuda.dll.so'
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b'legacy SveSop bridge must not ship')
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
                if part == 'bridges':
                    (tree / 'provenance/WINE-SCOPE.json').write_text(json.dumps(
                        {'mode': 'x86_64-unix-ntdll', 'ge_preserved_sha256': stock,
                         'ge_preserved_links': {}}))
                for name in overlay.required_payload():
                    if name in ['proton', 'proton_zluda.py'] or '/wine-cuda/stub/' in name or name.endswith('stubs.json'):
                        continue
                    if (name.startswith('files/lib64/')) != (part == 'zluda'):
                        continue
                    path = tree / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if '/wine-cuda/zluda/' in name:
                        path.write_bytes(elf() if name.endswith('.so') else
                                         pe(0x8664, 788) if 'x86_64-windows' in name else pe(0x14c, 690))
                    else:
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
                 mock.patch.object(overlay, 'extract', extract), \
                 mock.patch.object(cuda_package.policy, 'STUB_SHA256', stub_hashes):
                overlay.assemble(overlays)
                dest = root / 'result'
                inputs.verify_manifest(dest)
                self.assertEqual((dest / 'protonfixes/changed.py').read_text(), 'new')
                self.assertFalse((dest / 'protonfixes/deleted.py').exists())
                self.assertEqual((dest / 'proton_zluda.py').read_text(), 'helper')
                self.assertTrue((dest / 'files/lib/wine/nvidia-libs/nvcuda').exists())
                self.assertIn('GE-Proton11-7-zluda', (dest / 'compatibilitytool.vdf').read_text())
                shutil.rmtree(dest)
                shutil.rmtree(build)
                build.mkdir()
                (trees['bridges.tar.gz'] / 'files/lib/wine-cuda/zluda/i386-windows/nvcuda.dll').unlink()
                inputs.manifest(trees['bridges.tar.gz'])
                with self.assertRaisesRegex(ValueError, 'three-module contract'):
                    overlay.assemble(overlays)


if __name__ == '__main__':
    unittest.main()
