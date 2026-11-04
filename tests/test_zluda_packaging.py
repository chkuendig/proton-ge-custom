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
    def test_full_tool_and_bridge_build_share_one_component_resolver(self):
        recipe = (inputs.HERE / 'build-components.sh').read_text()
        self.assertLess(recipe.index('build-wine.sh'), recipe.index('build-bridge.sh'))
        self.assertIn('bridge_input.py" package', recipe)
        self.assertIn('--source "$BRIDGE_COMPONENT_ROOT"', recipe)
        self.assertNotIn('/zluda/wine/', recipe)


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

    def test_full_valve_build_refuses_non_ci_before_network_or_compilation(self):
        env = dict(os.environ, CI='false', SOURCE_ROOT='/nonexistent', BUILD_ROOT='/nonexistent',
                   DOWNLOAD_ROOT='/nonexistent', WINE_SDK_ROOT='/nonexistent')
        result = subprocess.run(['bash', str(inputs.HERE / 'build-wine.sh')], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('hosted workflow', result.stderr)
        recipe = (inputs.HERE / 'build-wine.sh').read_text()
        self.assertIn('valve_tool.py" admit', recipe)
        self.assertIn('CONTAINER=1', recipe)
        self.assertIn('proton_base', recipe)
        self.assertNotIn('protonprep', recipe)





class WineTargets(unittest.TestCase):
    def fixture(self, arch):
        return (ROOT / 'tests/fixtures' / ('wine-' + arch + '.mk')).read_text()

    def test_fixture_provenance_and_configured_source_names(self):
        provenance = json.loads((ROOT / 'tests/fixtures/wine-targets-source.json').read_text())
        self.assertEqual(provenance['wine'], '46b29104e3741fe23bf5e2547196a253aab88c89')  # Historical generated-target fixture
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
            self.assertLess(wine.index('valve_tool.py" admit'), wine.index(' -j"$jobs" dist'))


class NativePlan(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / 'tests/fixtures/native-graph.json').read_text())

    def test_resolved_workspace_specs_and_features(self):
        fixture = self.fixture()
        self.assertEqual(fixture['origin']['pin'], inputs.LOCK['components']['zluda'])
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
            (source / 'utilities.py').write_text('Vulkan helper')
            (source / 'vulkan.py').write_text('Vulkan binding')
            run(source, 'add', '.')
            run(source, 'commit', '-qm', 'tool sources')
            commit = run(source, 'rev-parse', 'HEAD')
            lock = copy.deepcopy(inputs.LOCK)
            ge = root / 'ge'
            ge.mkdir()
            tool = ge / 'GE-Proton11-7'
            tool.mkdir(parents=True)
            (tool / 'files/bin').mkdir(parents=True)
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
            (tool / 'compatibilitytool.vdf').write_text(
                '\n"GE-Proton11-7" // Internal name\n"display_name" "GE"\n')
            overlays = root / 'overlays'
            overlays.mkdir()
            (overlays / 'valve-stock.tar.gz').write_bytes(b'full Valve archive fixture')
            receipt = {'proton_pin': lock['proton_base'], 'wine_pin': lock['components']['wine'],
                       'sdk_image': lock['sdk_image'], 'nvcuda_sha256': stub_hashes,
                       'archive_sha256': inputs.sha256(overlays / 'valve-stock.tar.gz')}
            (overlays / 'VALVE-STOCK.json').write_text(json.dumps(receipt))
            (cache / 'winetricks').write_text('runtime script fixture')
            next(x for x in lock['downloads'] if x['id'] == 'winetricks')['sha256'] = inputs.sha256(cache / 'winetricks')
            trees = {}
            for part in ['zluda', 'bridges']:
                tree = root / ('tree-' + part)
                (tree / 'provenance').mkdir(parents=True)
                record = {'part': part, 'proton': commit,
                          'input_lock_sha256': inputs.sha256(inputs.HERE / 'inputs.lock.json')}
                (tree / 'provenance/inputs.lock.json').write_bytes((inputs.HERE / 'inputs.lock.json').read_bytes())
                (tree / 'provenance' / (part + '.json')).write_text(json.dumps(record))
                if part == 'bridges':
                    (tree / 'provenance/WINE-SCOPE.json').write_text(json.dumps(
                        {'mode': 'valve-single-ntdll', 'replaced': {}}))
                for name in overlay.required_payload():
                    if name in ['proton', 'proton_zluda.py', 'utilities.py', 'vulkan.py', 'files/bin/winetricks'] or '/wine-cuda/stub/' in name or name.endswith('stubs.json'):
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
                shutil.copytree(tool if archive.name == 'valve-stock.tar.gz' else trees[archive.name],
                                target, symlinks=True)
            env = {'SOURCE_ROOT': str(source), 'DOWNLOAD_ROOT': str(cache),
                   'BUILD_ROOT': str(build), 'DESTDIR': str(root / 'result')}
            with mock.patch.dict(os.environ, env), mock.patch.object(overlay, 'LOCK', lock), \
                 mock.patch.object(overlay, 'extract', extract), \
                 mock.patch.object(overlay.reuse_components, 'expected_commit', return_value=commit):
                overlay.assemble(overlays)
                dest = root / 'result'
                inputs.verify_manifest(dest)
                self.assertFalse((dest / 'protonfixes').exists())
                self.assertEqual((dest / 'proton_zluda.py').read_text(), 'helper')
                self.assertEqual((dest / 'utilities.py').read_text(), 'Vulkan helper')
                self.assertFalse((dest / 'files/lib/wine/nvidia-libs/nvcuda').exists())
                self.assertIn('Valve-Proton11.0-2e-zluda', (dest / 'compatibilitytool.vdf').read_text())
                shutil.rmtree(dest)
                shutil.rmtree(build)
                build.mkdir()
                # Reusing an unchanged bridge set must preserve its original
                # lock while the assembled candidate carries today's lock.
                bridge = trees['bridges.tar.gz']
                old_commit = 'b' * 40
                old_lock = bridge / 'provenance/inputs.lock.json'
                old_lock.write_text('{"native_backend":"superseded"}\n')
                old_hash = inputs.sha256(old_lock)
                record = json.loads((bridge / 'provenance/bridges.json').read_text())
                record.update(proton=old_commit, input_lock_sha256=old_hash)
                (bridge / 'provenance/bridges.json').write_text(json.dumps(record))
                inputs.manifest(bridge)
                receipt = root / 'REUSE.json'
                receipt.write_text(json.dumps({'commit': old_commit, 'part': 'bridges'}))
                with mock.patch.dict(os.environ, {'REUSE_BRIDGES_COMMIT': old_commit,
                                                  'COMPONENT_REUSE_RECEIPT': str(receipt)}), \
                     mock.patch.object(overlay.reuse_components, 'expected_commit',
                                       side_effect=lambda source, part: old_commit if part == 'bridges' else commit), \
                     mock.patch.object(overlay.reuse_components, 'expected_lock_sha256',
                                       side_effect=lambda source, part: old_hash if part == 'bridges' else inputs.sha256(inputs.HERE / 'inputs.lock.json')):
                    overlay.assemble(overlays)
                self.assertEqual(inputs.sha256(dest / 'provenance/inputs.lock.json'),
                                 inputs.sha256(inputs.HERE / 'inputs.lock.json'))
                self.assertEqual(inputs.sha256(dest / 'provenance/inputs-bridges.lock.json'), old_hash)
                provenance = json.loads((dest / 'provenance/PROVENANCE.json').read_text())
                self.assertEqual(provenance['component_build_protons'], {'zluda': commit, 'bridges': old_commit})
                inputs.verify_manifest(dest)
                shutil.rmtree(dest)
                shutil.rmtree(build)
                build.mkdir()
                record.update(proton=commit, input_lock_sha256=inputs.sha256(inputs.HERE / 'inputs.lock.json'))
                (bridge / 'provenance/bridges.json').write_text(json.dumps(record))
                old_lock.write_bytes((inputs.HERE / 'inputs.lock.json').read_bytes())
                (trees['bridges.tar.gz'] / 'files/lib/wine-cuda/zluda/i386-windows/nvcuda.dll').unlink()
                inputs.manifest(trees['bridges.tar.gz'])
                with self.assertRaisesRegex(ValueError, 'three-module contract'):
                    overlay.assemble(overlays)


if __name__ == '__main__':
    unittest.main()
