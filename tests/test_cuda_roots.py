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
        self.assertLess(text.index('g_proton.make_default_prefix()'), text.index('g_session.init_session(sys.argv'))
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

    def test_missing_default_template_wineboot_materializes_regular_stubs(self):
        import contextlib
        prefix = self.tool / 'files/share/default_pfx'
        calls = []
        def run(args, env):
            calls.append((args, dict(env)))
            if args[-1] == 'wineboot':
                for directory, arch in policy.ARCHES.items():
                    path = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
                    path.parent.mkdir(parents=True)
                    path.symlink_to(self.cuda / 'stub' / arch / 'nvcuda.dll')
            return 0
        env = {'PROTON_ENABLE_ZLUDA': '1'}
        policy.select_root(env, self.tool, bootstrap=True)
        method = self.method('Proton', 'make_default_prefix', {'g_session': SimpleNamespace(env=env, run_proc=run)})
        proton = SimpleNamespace(base_dir=str(self.tool), default_pfx_dir=str(prefix),
                                 dist_lock=contextlib.nullcontext(), missing_default_prefix=lambda: True,
                                 wine_bin='/bin/wine', wineserver_bin='/bin/wineserver')
        method(proton)
        policy.validate_default_prefix(self.tool, prefix)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(env['WINEDLLPATH'] == str(self.cuda / 'stub') for args, env in calls))

    def test_existing_template_validation_cannot_race_creator_dist_lock(self):
        locked = []
        class Lock:
            def __enter__(self):
                locked.append(True)
            def __exit__(self, *args):
                locked.pop()
        method = self.method('Proton', 'make_default_prefix', {'g_session': SimpleNamespace(env={})})
        proton = SimpleNamespace(base_dir=self.tool, default_pfx_dir='/template', dist_lock=Lock(),
                                 missing_default_prefix=lambda: False)
        def validate(*args):
            self.assertEqual(locked, [True])
        with mock.patch.object(policy, 'validate_default_prefix', side_effect=validate) as check:
            method(proton)
        check.assert_called_once_with(self.tool, '/template')
        self.assertEqual(locked, [])

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
        self.assertLess(text.index('with self.prefix_lock:', start), text.index('prepare_prefix(', start))
        compat2 = self.root / 'regular'
        regular = compat2 / 'pfx/drive_c/windows/system32/nvcuda.dll'
        regular.parent.mkdir(parents=True)
        regular.write_bytes(b'\x7fELFfixture managed bridge')
        policy.migrate_prefix(self.tool, compat2)
        self.assertEqual(regular.read_bytes(), (self.cuda / 'stub/x86_64-windows/nvcuda.dll').read_bytes())

    def test_migration_classifies_builtin_pe_elf_symlinks_and_native_user_pe(self):
        import struct
        for index, kind in enumerate(['builtin', 'placeholder', 'elf', 'native', 'unknown', 'bad-pe', 'user-link']):
            compat = self.root / ('migration-' + kind)
            path = compat / 'pfx/drive_c/windows/system32/nvcuda.dll'
            path.parent.mkdir(parents=True)
            data = bytearray(512)
            data[:2] = b'MZ'
            struct.pack_into('<I', data, 0x3c, 256)
            data[256:260] = b'PE\0\0'
            if kind in ['builtin', 'placeholder', 'bad-pe']:
                marker = b'Wine placeholder DLL' if kind == 'placeholder' else b'Wine builtin DLL'
                data[0x40:0x40 + len(marker)] = marker
            if kind == 'bad-pe':
                data[256:260] = b'noPE'
            elif kind == 'elf':
                data[:4] = b'\x7fELF'
            elif kind == 'unknown':
                data[:2] = b'??'
            if kind == 'user-link':
                path.symlink_to('/user/drivers/nvcuda.dll')
            else:
                path.write_bytes(data)
            logs = []
            policy.migrate_prefix(self.tool, compat, log=logs.append)
            managed = kind in ['builtin', 'placeholder', 'elf']
            self.assertEqual(path.is_symlink(), kind == 'user-link')
            if managed:
                self.assertEqual(path.read_bytes(), (self.cuda / 'stub/x86_64-windows/nvcuda.dll').read_bytes())
                self.assertEqual(logs, [])
            elif kind != 'user-link':
                self.assertEqual(path.read_bytes(), data)
            self.assertEqual(bool(logs), not managed)
            record = json.loads((compat / 'zluda-cuda-roots-v1.json').read_text())
            self.assertEqual(bool(record['preserved']), not managed)

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
        self.assertNotIn('old_nvapi', text)
        self.assertNotIn('prepare_nvapi', text)

    def test_missing_backend_falls_back_and_flag_and_classic_x86_warnings(self):
        (self.tool / 'files/lib64/libcuda.so.1').unlink()
        logs = []
        env = {'PROTON_ENABLE_ZLUDA': '1', 'ZLUDA_WINE_BACKEND_PATH': '/inherited'}
        self.assertEqual(policy.select_root(env, self.tool, log=logs.append).name, 'stub')
        self.assertEqual(env['PROTON_ENABLE_ZLUDA'], '0')
        self.assertNotIn('ZLUDA_WINE_BACKEND_PATH', env)
        self.assertIn('continuing with ZLUDA off', logs[0])
        exe = self.root / 'probe.exe'
        exe.write_bytes(b'MZ' + bytes(58) + (64).to_bytes(4, 'little') + b'PE\0\0\x4c\x01')
        for flag in ['PROTON_NVIDIA_NVCUDA', 'PROTON_NVIDIA_LIBS', 'PROTON_NVIDIA_NVOPTIX']:
            logs = []
            policy.launch_warnings({flag: '1', 'PROTON_ENABLE_ZLUDA': '1'}, [str(exe)], logs.append)
            self.assertIn('not supported', logs[0])
            self.assertIn('PROTON_USE_WOW64=1 is required', logs[1])
        logs = []
        policy.launch_warnings({'PROTON_ENABLE_ZLUDA': '1', 'WINEARCH': 'wow64'}, [str(exe)], logs.append)
        self.assertEqual(logs, [])

    def test_update_wineboot_is_stub_only_under_lock_and_runs_once(self):
        compat = self.root / 'compat'
        prefix = compat / 'pfx'
        prefix.mkdir(parents=True)
        inf = self.tool / 'wine.inf'
        inf.write_text('version')
        proton = SimpleNamespace(base_dir=self.tool, wine_inf=inf, wine_bin='/wine', wineserver_bin='/server')
        data = SimpleNamespace(base_dir=compat, prefix_dir=str(prefix))
        env = {'PROTON_ENABLE_ZLUDA': '1', 'WINEARCH': 'wow64'}
        policy.select_root(env, self.tool)
        calls = []
        session = SimpleNamespace(env=env, run_proc=lambda args, local: calls.append((args, local)) or 0)
        policy.prepare_prefix(proton, data, session)
        self.assertEqual([args for args, local in calls], [['/wine', 'wineboot', '--update'], ['/server', '-w']])
        self.assertTrue(all(local['WINEDLLPATH'].split(':')[0] == str(self.cuda / 'stub') for args, local in calls))
        self.assertTrue(all('ZLUDA_WINE_BACKEND_PATH' not in local for args, local in calls))
        self.assertEqual(env['WINEDLLPATH'], str(self.cuda / 'zluda'))
        policy.prepare_prefix(proton, data, session)
        self.assertEqual(len(calls), 2)


class PublishedNvapiEquivalence(unittest.TestCase):
    def test_off_and_on_match_published_launcher_including_late_gamefix_force(self):
        import itertools
        import textwrap
        fixture = json.loads((ROOT / 'tests/fixtures/published-nvapi-policy.json').read_text())
        tree = ast.parse((ROOT / 'proton').read_text())
        session_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Session')
        init = next(n for n in session_class.body if isinstance(n, ast.FunctionDef) and n.name == 'init_session')
        setup = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'setup_prefix')
        def source(nodes):
            return '\n'.join(ast.unparse(n) for n in nodes)
        flags = [n for n in init.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                 and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == 'check_environment'
                 and n.value.args and isinstance(n.value.args[0], ast.Constant)
                 and n.value.args[0].value in ('PROTON_DISABLE_NVAPI', 'PROTON_FORCE_NVAPI')]
        hide = [n for n in init.body if isinstance(n, ast.If) and ast.unparse(n.test).startswith("'hidenvgpu' in")]
        env = [n for n in init.body if isinstance(n, ast.If) and (
               ast.unparse(n.test).startswith("'vkreflex' in") or
               ast.unparse(n.test).startswith("'disablenvapi' not in") or
               ast.unparse(n.test) == "'forcenvapi' in self.compat_config")]
        prefix = [n for n in ast.walk(setup) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'use_nvapi' for t in n.targets)]
        copies = [n for n in ast.walk(setup) if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                  and n.test.id == 'use_nvapi']
        current = dict(flags=source(flags), hide=source(hide), env=source(env),
                       prefix=source(prefix), copy=source(copies))
        self.assertTrue(all(current.values()))
        policies = [{k: compile(p[k], fixture['source'] if p is fixture else 'current', 'exec')
                     for k in current} for p in (fixture, current)]
        text = (ROOT / 'proton').read_text()
        # The only late NVAPI writes must be the published final env assignments.
        late_if = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                       and ast.unparse(n.test) == 'zluda_enabled(g_session.env)')
        writes = [n for n in late_if.body if isinstance(n, ast.Assign) and
                  any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and
                      t.slice.value in ('PROTON_FORCE_NVAPI', 'DXVK_ENABLE_NVAPI',
                                        'DXVK_NVAPI_ALLOW_OTHER_DRIVERS') for t in n.targets)]
        late = compile(source(writes), 'current-late-nvapi', 'exec')
        published_late = compile(fixture['late'], fixture['source'], 'exec')
        self.assertNotIn('prepare_nvapi', text)
        self.assertNotIn('_cuda_hide', text)
        self.assertEqual(text.count('g_compatdata.setup_prefix()'), 1)
        for disabled, forced, hidden, wow64, libs, late_force, arch, enable in itertools.product(
                [None, '0', '1'], [None, '0', '1'], [False, True], [False, True], [False, True],
                [None, '0', '1'], ['x86_64-windows', 'aarch64-windows'], ['0', 'early', 'late']):
            results = []
            for index, program in enumerate(policies):
                config = {flag for flag, value in [('hidenvgpu', hidden), ('wow64', wow64),
                           ('nvidialibs', libs)] if value}
                env = {'PROTON_ENABLE_ZLUDA': '1' if enable == 'early' else '0', 'WINEDLLOVERRIDES': 'user=n',
                       'WINE_HIDE_NVIDIA_GPU': 'user', 'DXVK_ENABLE_NVAPI': 'user'}
                for key, value in [('PROTON_DISABLE_NVAPI', disabled), ('PROTON_FORCE_NVAPI', forced)]:
                    if value is not None:
                        env[key] = value
                session = SimpleNamespace(env=env, compat_config=config, dlloverrides={})
                def check_environment(key, flag):
                    if key in env:
                        config.add(flag) if env[key] not in ('', '0') else config.discard(flag)
                session.check_environment = check_environment
                operations = []
                ns = {'self': session, 'g_session': session, 'nvapi_path': 'nvapi/', 'tracked_files': None,
                      'g_proton': SimpleNamespace(host_pe_arch=arch, dist_dir='/tool/files/',
                           arch_pe_dir=lambda path, x86: '/tool/' + path + ('i386/' if x86 else 'x86_64/')),
                      'utilities': SimpleNamespace(is_driver_loaded=lambda drivers: False),
                      'try_copy': lambda src, dst, **kw: operations.append(('copy', src, dst)),
                      'file_exists': lambda *a, **kw: True,
                      'os': SimpleNamespace(unlink=lambda path: operations.append(('unlink', path))),
                      'g_compatdata': SimpleNamespace(setup_prefix=lambda: operations.append(('resync',))),
                      'sys': SimpleNamespace(argv=['proton', 'run'])}
                session.prefix_dir = '/compat/pfx/'
                for section in ('flags', 'hide', 'env', 'prefix', 'copy'):
                    exec(program[section], ns)
                if late_force is not None:
                    env['PROTON_FORCE_NVAPI'] = late_force
                if enable != '0':
                    env['PROTON_ENABLE_ZLUDA'] = '1'
                    exec(late if index else published_late, ns)
                # CUDA override ownership deliberately moved to root selection;
                # compare every NVAPI override, copy/removal and environment value.
                session.dlloverrides.pop('nvcuda', None)
                results.append((env, config, session.dlloverrides, operations))
            self.assertEqual(*results, msg=str((disabled, forced, hidden, wow64, libs, late_force, arch, enable)))
