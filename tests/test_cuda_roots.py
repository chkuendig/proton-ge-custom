"""CPU-only policy fixtures: no Wine process or compiled bridge is executed."""
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import proton_zluda as policy

ROOT = Path(__file__).resolve().parents[1]


class CudaRoots(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tool = self.root / 'tool'
        self.cuda = self.tool / 'files/lib/wine-cuda'
        hashes = {}
        for arch in policy.ARCHES.values():
            data = ('fixture GE stub ' + arch).encode()
            path = self.cuda / 'stub' / arch / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            hashes[arch] = hashlib.sha256(data).hexdigest()
            on = self.cuda / 'zluda' / arch / 'nvcuda.dll'
            on.parent.mkdir(parents=True)
            on.write_bytes(b'fixture builtin PE')
        native = self.cuda / 'zluda/x86_64-unix/nvcuda.so'
        native.parent.mkdir()
        native.write_bytes(b'fixture unixlib')
        backend = self.tool / 'files/lib64/libcuda.so.1'
        backend.parent.mkdir()
        backend.write_bytes(b'fixture backend')
        (self.cuda / 'stubs.json').write_text(json.dumps(
            {'ge_archive_sha256': policy.GE_ARCHIVE_SHA256, 'sha256': hashes}))
        # Fixtures test pinned-hash enforcement with tiny synthetic payloads.
        self.patch = mock.patch.object(policy, 'STUB_SHA256', hashes)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_off_on_off_with_inherited_roots_overrides_and_children(self):
        env = {'WINEDLLPATH': ':'.join(['/other/files/lib/wine-cuda/zluda', str(self.cuda / 'stub'),
                                      '/unmanaged', str(self.cuda / 'zluda/../zluda')]),
               'WINEDLLOVERRIDES': 'd3d11,nvcuda=n;*NvCuDa.DLL=n,b;foo=b',
               'ZLUDA_WINE_BACKEND_PATH': '/host/driver'}
        baseline = {p: p.read_bytes() for p in self.tool.rglob('*') if p.is_file()}
        for mode in ['0', '1', '0']:
            env['PROTON_ENABLE_ZLUDA'] = mode
            env['LD_LIBRARY_PATH'] = str(self.tool / 'files/lib64') + ':/unmanaged'
            selected = policy.select_root(env, self.tool)
            self.assertEqual(env['WINEDLLPATH'].split(':'), [str(selected), '/unmanaged'])
            self.assertEqual(selected.name, 'zluda' if mode == '1' else 'stub')
            self.assertEqual(env['WINEDLLOVERRIDES'], 'd3d11=n;foo=b;nvcuda=b')
            self.assertEqual(env['LD_LIBRARY_PATH'], '/unmanaged')
            self.assertEqual('ZLUDA_WINE_BACKEND_PATH' in env, mode == '1')
            if mode == '1':
                self.assertEqual(env['ZLUDA_WINE_BACKEND_PATH'], str(self.tool / 'files/lib64/libcuda.so.1'))
            child = dict(env)
            policy.select_root(child, self.tool, bootstrap=True)
            self.assertEqual(child['WINEDLLPATH'].split(':')[0], str(self.cuda / 'stub'))
            self.assertNotIn('ZLUDA_WINE_BACKEND_PATH', child)
            self.assertEqual(env['WINEDLLPATH'].split(':')[0], str(selected))
        self.assertEqual(baseline, {p: p.read_bytes() for p in baseline})

    def test_missing_or_corrupt_roots_fail_closed_before_bootstrap(self):
        path = self.cuda / 'stub/i386-windows/nvcuda.dll'
        path.write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'stub hash'):
            policy.select_root({}, self.tool, bootstrap=True)
        path.write_bytes(b'fixture GE stub i386-windows')
        path.unlink()
        path.symlink_to(self.cuda / 'stub/x86_64-windows/nvcuda.dll')
        with self.assertRaisesRegex(ValueError, 'regular-file'):
            policy.select_root({}, self.tool)
        path.unlink()
        path.write_bytes(b'fixture GE stub i386-windows')
        (self.cuda / 'zluda/i386-windows/nvcuda.dll').unlink()
        policy.select_root({}, self.tool)  # off needs no functional bridge
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            policy.select_root({'PROTON_ENABLE_ZLUDA': '1'}, self.tool)

    def method(self, class_name, method, namespace):
        tree = ast.parse((ROOT / 'proton').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
        function = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / 'proton'), 'exec'), namespace)
        return namespace[method]

    def test_real_init_wine_bootstraps_stub_for_wow64_before_any_wineboot(self):
        def prepend(env, key, value, separator):
            env[key] = value + (separator + env[key] if env.get(key) else '')
        proton = SimpleNamespace(base_dir=str(self.tool), dist_dir='/dist/', media_dir='/media/',
                                 lib_dir=str(self.tool / 'files/lib') + '/', bin_dir='/bin/', host_pe_arch='x86_64-windows')
        method = self.method('Session', 'init_wine', {'os': os, 'platform': platform,
                             'g_proton': proton, 'ld_path_var': 'LD_LIBRARY_PATH', 'prepend_to_env_str': prepend})
        with mock.patch.dict(os.environ, {'WINEDLLPATH': str(self.cuda / 'zluda') + ':/extra'}, clear=True), \
             mock.patch.object(platform, 'machine', return_value='x86_64'):
            session = SimpleNamespace(env={'PROTON_ENABLE_ZLUDA': '1', 'PROTON_USE_WOW64': '1'})
            method(session)
        self.assertEqual(session.env['WINEARCH'], 'wow64')
        self.assertEqual(session.env['WINEDLLPATH'].split(':'), [str(self.cuda / 'stub'),
                         str(self.tool / 'files/lib/vkd3d'), str(self.tool / 'files/lib/wine'), '/extra'])
        self.assertNotIn('ZLUDA_WINE_BACKEND_PATH', session.env)
        text = (ROOT / 'proton').read_text()
        self.assertLess(text.index('g_session.init_wine()'), text.index('g_proton.make_default_prefix()'))
        self.assertLess(text.index('validate_default_prefix(g_proton.base_dir'), text.index('g_session.init_session(sys.argv'))
        self.assertGreater(text.index('select_root(g_session.env'), text.index('protonfixes.execute()'))
        self.assertLess(text.index('select_root(g_session.env'), text.index('#determine mode'))

    def test_default_template_regular_files_and_pfx_copy_rejects_root_symlinks(self):
        default = self.root / 'default_pfx'
        for directory, arch in policy.ARCHES.items():
            path = default / 'drive_c/windows' / directory / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.write_bytes((self.cuda / 'stub' / arch / 'nvcuda.dll').read_bytes())
        policy.validate_default_prefix(self.tool, default)
        path.unlink()
        path.symlink_to(self.cuda / 'stub' / arch / 'nvcuda.dll')
        with self.assertRaisesRegex(ValueError, 'regular GE CUDA stubs'):
            policy.validate_default_prefix(self.tool, default)
        method = self.method('CompatData', 'pfx_copy', {'os': os})
        with self.assertRaisesRegex(ValueError, 'regular pinned GE stub'):
            method(None, str(path), str(self.root / 'destination'))

    def test_one_time_legacy_migration_preserves_unknown_user_files(self):
        compat = self.root / 'compat'
        prefix = compat / 'pfx'
        x64 = prefix / 'drive_c/windows/system32/nvcuda.dll'
        x86 = prefix / 'drive_c/windows/syswow64/nvcuda.dll'
        for path in [x64, x86]:
            path.parent.mkdir(parents=True)
        old = self.root / 'old-tool/files/lib/wine/x86_64-unix/nvcuda.dll.so'
        x64.symlink_to(old)
        x86.write_bytes(b'user PE or unknown ELF')
        policy.migrate_prefix(self.tool, compat)
        self.assertFalse(x64.is_symlink())
        self.assertEqual(x64.read_bytes(), (self.cuda / 'stub/x86_64-windows/nvcuda.dll').read_bytes())
        self.assertEqual(x86.read_bytes(), b'user PE or unknown ELF')
        x64.unlink()
        x64.symlink_to(old)
        policy.migrate_prefix(self.tool, compat)
        self.assertTrue(x64.is_symlink())  # versioned migration does not swap on/off
        text = (ROOT / 'proton').read_text()
        start = text.index('    def setup_prefix(self):')
        self.assertLess(text.index('with self.prefix_lock:', start), text.index('migrate_prefix(', start))
        compat2 = self.root / 'regular'
        regular = compat2 / 'pfx/drive_c/windows/system32/nvcuda.dll'
        regular.parent.mkdir(parents=True)
        regular.write_bytes(b'known managed ELF')
        with mock.patch.object(policy, 'LEGACY_PREFIX_SHA256', {hashlib.sha256(regular.read_bytes()).hexdigest()}):
            policy.migrate_prefix(self.tool, compat2)
        self.assertEqual(regular.read_bytes(), (self.cuda / 'stub/x86_64-windows/nvcuda.dll').read_bytes())

    def test_ge_nvidia_flags_do_not_select_or_copy_cuda_and_nvapi_stays_separate(self):
        for flag in ['PROTON_NVIDIA_NVCUDA', 'PROTON_NVIDIA_LIBS', 'PROTON_NVIDIA_NVENC',
                     'PROTON_NVIDIA_NVML', 'PROTON_NVIDIA_NVOPTIX', 'PROTON_FORCE_NVAPI',
                     'PROTON_DISABLE_NVAPI', 'PROTON_USE_WOW64']:
            env = {flag: '1'}
            self.assertEqual(policy.select_root(env, self.tool).name, 'stub')
            env['PROTON_ENABLE_ZLUDA'] = '1'
            self.assertEqual(policy.select_root(env, self.tool).name, 'zluda')
            if flag in ['PROTON_FORCE_NVAPI', 'PROTON_DISABLE_NVAPI']:
                self.assertEqual(env[flag], '1')
        text = (ROOT / 'proton').read_text()
        self.assertNotIn('use_nvcuda', text)
        self.assertNotIn('wine/nvidia-libs/nvcuda', text)
        for name in ['nvml', 'nvenc', 'nvoptix']:
            self.assertIn('wine/nvidia-libs/' + name, text)
        self.assertIn('if old_nvapi != new_nvapi', text)
        self.assertIn('prepare_nvapi(self.env, self.compat_config)', text)

    def test_nvapi_config_matrix_and_late_prefix_decision(self):
        import itertools
        for disabled, forced, wow64, libs in itertools.product([False, True], repeat=4):
            config = {name for name, state in [('disablenvapi', disabled), ('forcenvapi', forced),
                      ('wow64', wow64), ('nvidialibs', libs)] if state}
            self.assertEqual(policy.nvapi_enabled(config, 'x86_64-windows'), not disabled or forced)
            self.assertFalse(policy.nvapi_enabled(config, 'aarch64-windows'))
        # Execute the actual late-fix synchronization block with a prefix-call
        # recorder: a new forced NVAPI must reach setup_prefix and its overrides.
        text = (ROOT / 'proton').read_text()
        start = text.index('    from proton_zluda import dll_override, nvapi_enabled, prepare_nvapi')
        end = text.index('    g_compatdata.setup_ddraw(', start)
        import textwrap
        calls = []
        session = SimpleNamespace(env={'PROTON_FORCE_NVAPI': '1'}, compat_config={'disablenvapi'})
        def check_environment(key, flag):
            if key in session.env:
                session.compat_config.add(flag) if session.env[key] == '1' else session.compat_config.discard(flag)
        session.check_environment = check_environment
        ns = {'g_session': session, 'g_proton': SimpleNamespace(host_pe_arch='x86_64-windows'),
              'g_compatdata': SimpleNamespace(setup_prefix=lambda: calls.append(set(session.compat_config))),
              'sys': SimpleNamespace(argv=['proton', 'run'])}
        exec(compile(textwrap.dedent(text[start:end]), 'late-nvapi-block', 'exec'), ns)
        self.assertIn('forcenvapi', calls[0])
        self.assertEqual(session.env['DXVK_ENABLE_NVAPI'], '1')
        self.assertIn('nvapi=n', session.env['WINEDLLOVERRIDES'])
        session.env['PROTON_FORCE_NVAPI'] = '0'
        exec(compile(textwrap.dedent(text[start:end]), 'late-nvapi-block', 'exec'), ns)
        self.assertEqual(session.env['DXVK_ENABLE_NVAPI'], '0')
        self.assertEqual(len(calls), 2)

    def test_zluda_on_implies_nvapi_without_an_extra_user_switch(self):
        for config in [set(), {'disablenvapi'}, {'disablenvapi', 'hidenvgpu', 'wow64'}]:
            env = {'PROTON_ENABLE_ZLUDA': '1'}
            policy.prepare_nvapi(env, config)
            self.assertEqual(env['PROTON_FORCE_NVAPI'], '1')
            self.assertEqual(env['DXVK_ENABLE_NVAPI'], '1')
            self.assertEqual(env['DXVK_NVAPI_ALLOW_OTHER_DRIVERS'], '1')
            self.assertEqual(env['DXVK_NVAPI_DRIVER_VERSION'], '99999')
            self.assertEqual(env['WINE_HIDE_AMD_GPU'], '1')
            self.assertIn('forcenvapi', config)
            self.assertTrue(policy.nvapi_enabled(config, 'x86_64-windows'))
            self.assertFalse('hidenvgpu' in config and 'forcenvapi' not in config)
        text = (ROOT / 'proton').read_text()
        self.assertLess(text.index('prepare_nvapi(self.env, self.compat_config)'),
                        text.index('self.check_environment("PROTON_FORCE_NVAPI"'))
        import textwrap
        start = text.index('    from proton_zluda import dll_override, nvapi_enabled, prepare_nvapi')
        end = text.index('    g_compatdata.setup_ddraw(', start)
        calls = []
        session = SimpleNamespace(env={'PROTON_ENABLE_ZLUDA': '1', 'WINE_HIDE_NVIDIA_GPU': '1'},
                                  compat_config={'disablenvapi', 'hidenvgpu'},
                                  _cuda_previous_nvidia_hide=None, _cuda_hide_written=True)
        def check_environment(key, flag):
            if key in session.env:
                session.compat_config.add(flag) if session.env[key] == '1' else session.compat_config.discard(flag)
        session.check_environment = check_environment
        ns = {'g_session': session, 'g_proton': SimpleNamespace(host_pe_arch='x86_64-windows'),
              'g_compatdata': SimpleNamespace(setup_prefix=lambda: calls.append(policy.nvapi_enabled(
                   session.compat_config, 'x86_64-windows'))), 'sys': SimpleNamespace(argv=['proton', 'run'])}
        exec(compile(textwrap.dedent(text[start:end]), 'late-zluda-nvapi', 'exec'), ns)
        self.assertEqual(calls, [True])
        self.assertIn('nvapi=n', session.env['WINEDLLOVERRIDES'])
        self.assertNotIn('WINE_HIDE_NVIDIA_GPU', session.env)
