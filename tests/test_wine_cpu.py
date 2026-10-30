"""Hosted CPU-gate orchestration fixtures; no Wine or compiler execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import wine_cpu as gate
import cpu_fixtures


class WineCpu(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.tool, self.work = [self.root / name for name in ['src', 'tool', 'work']]
        self.source.mkdir()
        self.tool.mkdir()
        self.wine = self.tool / 'files/bin/wine'
        self.server = self.tool / 'files/bin/wineserver'

    def results(self, directory, winearch='win64'):
        directory.mkdir(parents=True)
        record = {'qualification': gate.PROFILES[winearch]['qualification'], 'architecture': gate.architecture(winearch),
                  'cases': [{'mode': mode, 'returncode': 0} for mode in gate.MODES],
                  'cleanup': {'verified_absent': True, 'lock': {'absent': True},
                              'steps': [{'command': [str(self.server), flag], 'returncode': code,
                                         'accepted': True} for flag, code in [('-k', 1), ('-w', 0), ('-k0', 1)]]}}
        if winearch == 'wow64':
            for case in record['cases']:
                case['architecture'] = {'process_machine': '0x014c', 'native_machine': '0x8664',
                    'pointer_bits': 32, 'module_machine': '0x014c', 'module_bits': 32,
                    'cu_deviceptr_bits': 32, 'exports': 0 if case['mode'].startswith('off') else 690}
                case['events'] = []
                if case['mode'] == 'on':
                    case['events'] = ['backend-loaded', 'cuInit', 'cuInit', 'sparse-512MiB-pool',
                                      'bounded-interior-htod', 'bounded-interior-dtoh', 'pool-released', 'context-released']
                    case['wow64_witness'] = {'native_bits': 64, 'native_pid': 1234,
                        'unixlib_path': str(self.tool / 'files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so'),
                        'unixlib_mapped': True, 'i386_unix_cuda_mapped': False, 'unexpected_cuda_mapping': False,
                        'pool_reserved_bytes': 536870912, 'pool_native_base': 0x100000000,
                        'htod_offset': 260, 'dtoh_offset': 260, 'pool_released': True, 'context_released': True}
                    (directory / 'on-backend.json').write_text(json.dumps(case['wow64_witness']))
                    (directory / 'on-backend.log').write_text('\n'.join(case['events']) + '\n')
        (directory / 'RESULT.json').write_text(json.dumps(record))
        for name in ['bootstrap', *gate.MODES, 'wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']:
            (directory / (name + '.log')).write_text('' if name.startswith('wineserver-') else 'fixture log\n')
        return record

    def test_environment_excludes_inherited_roots_backends_and_gpu_libraries(self):
        inherited = dict(WINEDLLPATH='/old/on:/old/off', WINEPREFIX='/shared',
                         WINEDLLOVERRIDES='nvcuda=n', ZLUDA_WINE_BACKEND_PATH='/host/libcuda.so.1',
                         PROTON_ENABLE_ZLUDA='1', LD_LIBRARY_PATH='/rocm:/host',
                         LD_PRELOAD='/host/cuda.so', DISPLAY=':1', WAYLAND_DISPLAY='wayland-0',
                         CUDA_VISIBLE_DEVICES='0', HIP_VISIBLE_DEVICES='0', PATH='/usr/bin')
        with mock.patch.dict(os.environ, inherited, clear=True):
            env = gate.environment(self.tool, self.work, self.wine, self.server)
        for name in ['WINEDLLPATH', 'WINEDLLOVERRIDES', 'ZLUDA_WINE_BACKEND_PATH',
                     'PROTON_ENABLE_ZLUDA', 'LD_PRELOAD', 'DISPLAY', 'WAYLAND_DISPLAY',
                     'CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES']:
            self.assertNotIn(name, env)
        self.assertEqual(env['LD_LIBRARY_PATH'], ':'.join(str(self.tool / 'files/lib' / arch)
                         for arch in ['wine/x86_64-unix', 'x86_64-linux-gnu', 'i386-linux-gnu']))
        self.assertEqual(env['WINEPREFIX'], str(self.work / 'prefix'))
        self.assertEqual(env['WINESERVER'], str(self.server))
        self.assertEqual(env['WINEARCH'], 'win64')
        wow64 = gate.environment(self.tool, self.work / 'wow64', self.wine, self.server, 'wow64')
        self.assertEqual(wow64['WINEARCH'], 'wow64')
        self.assertEqual(wow64['WINEPREFIX'], str(self.work / 'wow64/prefix'))
        with self.assertRaises(ValueError):
            gate.environment(self.tool, self.work, self.wine, self.server, 'win32')

    def test_device_and_fake_backend_dependency_guards(self):
        dev = self.root / 'dev'
        dev.mkdir()
        gate.admit_cpu_devices(dev)
        for name in ['dri', 'kfd']:
            (dev / name).mkdir()
            with self.assertRaisesRegex(ValueError, 'GPU devices'):
                gate.admit_cpu_devices(dev)
            (dev / name).rmdir()
        backend = self.root / 'libcuda.so.1'
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        header[18:20] = (62).to_bytes(2, 'little')
        backend.write_bytes(header)
        dynamic = '(SONAME) Library soname: [libcuda.so.1]\n(NEEDED) Shared library: [libc.so.6]\n'
        with mock.patch.object(gate.subprocess, 'check_output', return_value=dynamic):
            self.assertEqual(gate.inspect_backend(backend), ['libc.so.6'])
        for extra in ['(NEEDED) Shared library: [libamdhip64.so.7]', '(RUNPATH) Library runpath: [/rocm]']:
            with mock.patch.object(gate.subprocess, 'check_output', return_value=dynamic + extra):
                with self.assertRaises(ValueError):
                    gate.inspect_backend(backend)

    def test_result_gate_requires_six_ordered_successes_cleanup_and_all_logs(self):
        results = self.root / 'results'
        valid = self.results(results)
        self.assertEqual(gate.check_result(results), valid)
        for invalid in [dict(valid, qualification='pending'), dict(valid, cases=valid['cases'][:-1]),
                        dict(valid, architecture=gate.architecture('wow64')),
                        dict(valid, architecture={}),
                        dict(valid, architecture=dict(valid['architecture'], native_bits=32)),
                        dict(valid, architecture=dict(valid['architecture'], unix_call_table='__wine_unix_call_wow64_funcs')),
                        dict(valid, cases=list(reversed(valid['cases']))),
                        dict(valid, cleanup={'returncode': 1}),
                        dict(valid, cleanup=dict(valid['cleanup'], verified_absent=False)),
                        dict(valid, cleanup=dict(valid['cleanup'], lock={'absent': False})),
                        dict(valid, cleanup=dict(valid['cleanup'], steps=[])),
                        dict(valid, cleanup=dict(valid['cleanup'], steps=[
                             dict(step, returncode=0) for step in valid['cleanup']['steps']])),
                        dict(valid, cases=[dict(case, returncode=1) for case in valid['cases']])]:
            (results / 'RESULT.json').write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                gate.check_result(results)
        (results / 'RESULT.json').write_text(json.dumps(valid))
        (results / 'wineserver-verify.log').write_text('permission denied')
        with self.assertRaisesRegex(ValueError, 'diagnostics'):
            gate.check_result(results)
        (results / 'wineserver-verify.log').write_text('')
        (results / 'off-again.log').unlink()
        with self.assertRaisesRegex(ValueError, 'log missing'):
            gate.check_result(results)

    def exercise(self, mode='pass', prepared=False):
        commands = []

        def command(argv, env, log, timeout, accepted=(0,)):
            argv = [str(value) for value in argv]
            commands.append((argv, env, timeout))
            log.write_text('orchestration fixture\n')
            if 'pinned_fixture_runner.py' in argv[1]:
                options = dict(zip(argv[6::2], argv[7::2]))
                arch = options['--winearch']
                Path(options['--prefix']).mkdir()
                if mode == 'timeout' or mode == 'wow64-timeout' and arch == 'wow64':
                    raise subprocess.TimeoutExpired(argv, timeout)
                if mode != 'missing-result' and not (mode == 'wow64-missing-result' and arch == 'wow64'):
                    self.results(Path(options['--results']), 'win64' if mode == 'wow64-wrong-profile' else arch)
            if (mode == 'kill-failure' or mode == 'wow64-cleanup-failure' and env['WINEARCH'] == 'wow64') and argv[-1] == '-k':
                raise RuntimeError('fixture kill failure')
            if argv[-1] in ['-k', '-w', '-k0']:
                log.write_text('')
            return 1 if argv[-1] == '-k0' else 0

        with mock.patch.object(gate, 'admit_cpu_devices'), \
             mock.patch.object(gate, 'admit_source', return_value=self.source / 'tests/abi'), \
             mock.patch.object(gate, 'admit_tool', return_value=[self.wine, self.server]), \
             mock.patch.object(gate, 'retain_production'), \
             mock.patch.object(gate, 'inspect_backend', return_value=['libc.so.6']), \
             mock.patch.object(gate, 'inspect_probe', return_value={'sha256': 'fixture'}), \
             mock.patch.object(cpu_fixtures, 'admit', return_value={'source_pin':'fixture'}) as admit, \
             mock.patch.object(gate, 'command', side_effect=command):
            if mode == 'pass':
                record = gate.run(self.source, self.tool, self.work,
                                  self.root/'sdk-fixtures' if prepared else None)
                self.assertEqual(record['status'], 'passed')
                self.assertEqual(record['qualification'], gate.PASS)
                self.assertEqual(record['case_count'], 12)
                self.assertEqual(set(record['profiles']), {'win64', 'wow64'})
                if prepared:
                    admit.assert_called_once_with(self.source,self.root/'sdk-fixtures',self.work/'build')
                    self.assertEqual(record['phases'], ['prepared-SDK-fixture-admission',
                        'six-case-win64-Wine-execution', 'six-case-wow64-Wine-execution'])
            else:
                with self.assertRaises((RuntimeError, ValueError, subprocess.TimeoutExpired, FileNotFoundError)):
                    gate.run(self.source, self.tool, self.work)
        record = json.loads((self.work / 'INTEGRATION.json').read_text())
        self.assertEqual(record['status'], 'passed' if mode == 'pass' else 'failed')
        # Wait and absence check must run even after probe timeout, absent
        # result or failure of the first cleanup call.
        self.assertEqual([entry[0] for entry in commands[-3:]],
                         [[str(self.server), flag] for flag in ['-k', '-w', '-k0']])
        self.assertTrue(all(env['WINEPREFIX'] == str(self.work / env['WINEARCH'] / 'prefix')
                            for _, env, _ in commands))
        self.assertEqual(record['profiles']['win64']['status'], 'passed' if mode.startswith('wow64-') or mode == 'pass' else 'failed')
        return commands

    def test_real_runner_contract_and_outer_cleanup(self):
        commands = self.exercise()
        runners = [(argv, env) for argv, env, _ in commands if 'pinned_fixture_runner.py' in argv[1]]
        self.assertEqual([env['WINEARCH'] for _, env in runners], ['win64', 'wow64'])
        for (argv, env), (arch, profile) in zip(runners, gate.PROFILES.items()):
            options = dict(zip(argv[6::2], argv[7::2]))
            self.assertEqual(options, {'--wine': str(self.wine), '--wineserver': str(self.server),
                '--winearch': arch, '--dll-dir': str(self.tool / 'files/lib/wine'),
                '--on-root': str(self.tool / 'files/lib/wine-cuda/zluda'),
                '--off-root': str(self.tool / 'files/lib/wine-cuda/stub'),
                '--backend': str(self.work / 'build' / profile['backend']),
                '--probe': str(self.work / 'build' / profile['probe']),
                '--prefix': str(self.work / arch / 'prefix'), '--results': str(self.work / arch / 'results')})
        cleanups = [env['WINEARCH'] for argv, env, _ in commands if argv[-1] == '-k0']
        self.assertEqual(cleanups, ['win64', 'wow64'])
        # The first prefix is independently gone before the second starts.
        first_cleanup = next(i for i, (argv, _, _) in enumerate(commands) if argv[-1] == '-k0')
        second_runner = next(i for i, (argv, env, _) in enumerate(commands)
                             if 'pinned_fixture_runner.py' in argv[1] and env['WINEARCH'] == 'wow64')
        self.assertLess(first_cleanup, second_runner)

    def test_prebuilt_sdk_fixtures_are_admitted_without_an_assembly_compiler(self):
        commands=self.exercise(prepared=True)
        self.assertFalse(any('build-fixtures.sh' in value for argv,_,_ in commands for value in argv))

    def test_wow64_failure_cannot_be_hidden_by_x64_success(self):
        for mode in ['wow64-timeout', 'wow64-missing-result', 'wow64-wrong-profile', 'wow64-cleanup-failure']:
            with self.subTest(mode=mode):
                self.work = self.root / mode
                commands = self.exercise(mode)
                self.assertEqual([env['WINEARCH'] for argv, env, _ in commands if argv[-1] == '-k0'], ['win64', 'wow64'])

    def test_wow64_runtime_machine_and_translation_witness_fail_closed(self):
        results = self.root / 'results'
        valid = self.results(results, 'wow64')
        on_root = self.tool / 'files/lib/wine-cuda/zluda'
        self.assertEqual(gate.check_result(results, 'wow64', on_root), valid)
        cases = [('machine', lambda r: r['cases'][1]['architecture'].update(process_machine='0x8664')),
                 ('pointer-width', lambda r: r['cases'][1]['architecture'].update(cu_deviceptr_bits=64)),
                 ('off-exports', lambda r: r['cases'][0]['architecture'].update(exports=690)),
                 ('off-exports-type', lambda r: r['cases'][0]['architecture'].update(exports=False)),
                 ('native-bits', lambda r: r['cases'][1]['wow64_witness'].update(native_bits=32)),
                 ('native-base', lambda r: r['cases'][1]['wow64_witness'].update(pool_native_base=4096)),
                 ('native-pid', lambda r: r['cases'][1]['wow64_witness'].update(native_pid=True)),
                 ('pool-size', lambda r: r['cases'][1]['wow64_witness'].update(pool_reserved_bytes=32)),
                 ('copy-offset', lambda r: r['cases'][1]['wow64_witness'].update(htod_offset=256)),
                 ('module-path', lambda r: r['cases'][1]['wow64_witness'].update(unixlib_path='/host/nvcuda.so')),
                 ('classic-unix', lambda r: r['cases'][1]['wow64_witness'].update(i386_unix_cuda_mapped=True)),
                 ('foreign-cuda', lambda r: r['cases'][1]['wow64_witness'].update(unexpected_cuda_mapping=True)),
                 ('missing-release', lambda r: r['cases'][1]['wow64_witness'].update(context_released=False)),
                 ('missing-event', lambda r: r['cases'][1]['events'].pop()),
                 ('off-event', lambda r: r['cases'][0]['events'].append('backend-loaded')),
                 ('off-witness', lambda r: r['cases'][0].update(wow64_witness={}))]
        for name, mutate in cases:
            with self.subTest(name=name):
                record = json.loads(json.dumps(valid))
                mutate(record)
                (results / 'RESULT.json').write_text(json.dumps(record))
                with self.assertRaises(ValueError):
                    gate.check_result(results, 'wow64', on_root)
        (results / 'RESULT.json').write_text(json.dumps(valid))
        (results / 'on-backend.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'retained witness'):
            gate.check_result(results, 'wow64', on_root)
        (results / 'on-backend.json').write_text(json.dumps(valid['cases'][1]['wow64_witness']))
        (results / 'off-zero-backend.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'off/refused'):
            gate.check_result(results, 'wow64', on_root)

    def test_probe_header_and_import_admission_rejects_wrong_architecture(self):
        sys.path.insert(0, str(ROOT / 'tests/fixtures'))
        from cuda_binary import pe
        probe = self.root / 'probe.exe'
        for arch, machine in [('win64', 0x8664), ('wow64', 0x14c)]:
            data = bytearray(pe(machine, imports=['KERNEL32.dll']))
            data[0x40:0x50] = bytes(16)  # probes are ordinary MinGW executables
            probe.write_bytes(data)
            self.assertEqual(gate.inspect_probe(probe, arch)['machine'], machine)
            with self.assertRaisesRegex(ValueError, 'architecture'):
                gate.inspect_probe(probe, 'wow64' if arch == 'win64' else 'win64')
            probe.write_bytes(pe(machine, imports=['libgcc_s_dw2-1.dll']))
            with self.assertRaisesRegex(ValueError, 'DLL dependency'):
                gate.inspect_probe(probe, arch)

    def test_production_retention_keeps_module_bytes_contract_and_checksums(self):
        for name in gate.cuda_package.MODULES:
            path = self.tool / 'files/lib/wine-cuda/zluda' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('production ' + name).encode())
        path = self.tool / 'provenance/ZLUDA-WINE.json'
        path.parent.mkdir()
        path.write_text(json.dumps({'contract': {'source_commit': gate.LOCK['components']['zluda']}}))
        self.work.mkdir()
        gate.retain_production(self.tool, self.work)
        for name in gate.cuda_package.MODULES:
            self.assertEqual((self.work / 'production' / name).read_bytes(),
                             (self.tool / 'files/lib/wine-cuda/zluda' / name).read_bytes())
        self.assertEqual(json.loads((self.work / 'production/BUILD-CONTRACT.json').read_text()),
                         {'source_commit': gate.LOCK['components']['zluda']})
        self.assertEqual(len((self.work / 'production/SHA256SUMS').read_text().splitlines()), 3)

    def test_both_build_paths_share_compiler_observed_full_source_archive(self):
        for file in ['packaging/zluda-overlay/build-components.sh', 'make/zluda-wine-bridge.mk']:
            text = (ROOT / file).read_text()
            self.assertIn('source_archive.py', text)
            self.assertIn('zluda-wine/target', text)
            self.assertNotIn('archive HEAD wine cuda_types', text)

    def test_probe_timeout_still_kills_and_waits(self):
        self.exercise('timeout')

    def test_runner_success_without_result_fails_closed(self):
        self.exercise('missing-result')

    def test_cleanup_failure_cannot_pass_and_still_waits(self):
        self.exercise('kill-failure')

    def test_cleanup_accepts_absence_but_rejects_survivor_diagnostics_and_wait_failure(self):
        self.work.mkdir()
        cases = [('already-exited', [1, 0, 1], '', True),
                 ('killed', [0, 0, 1], '', True),
                 ('survivor', [0, 0, 0], '', False),
                 ('wait-failure', [1, 1, 1], '', False),
                 ('wait-timeout', [0, 'timeout', 0], '', False),
                 ('absence-diagnostic', [1, 0, 1], 'permission denied', False),
                 ('unexpected-code', [2, 0, 1], '', False)]
        for name, codes, diagnostic, passed in cases:
            with self.subTest(name=name):
                responses = iter(codes)
                def command(argv, env, log, timeout, accepted=(0,)):
                    code = next(responses)
                    log.write_text(diagnostic if code == 1 else '')
                    if code == 'timeout':
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if code not in accepted:
                        raise RuntimeError('unexpected returncode ' + str(code))
                    return code
                with mock.patch.object(gate, 'command', side_effect=command):
                    record = gate.cleanup_server(self.server, {'WINEPREFIX': str(self.work / 'prefix')}, self.work)
                self.assertEqual(record['status'], 'passed' if passed else 'failed')
                self.assertEqual([entry['command'][-1] for entry in record['commands']], ['-k', '-w', '-k0'])
                if passed:
                    self.assertEqual([entry['returncode'] for entry in record['commands']], codes)

    def test_direct_posix_lock_check_detects_a_survivor_without_wine(self):
        prefix = self.root / 'prefix'
        temporary = self.root / 'tmp'
        prefix.mkdir()
        temporary.mkdir()
        self.assertEqual(gate.server_lock_state(prefix, temporary)['state'], 'absent-lock-path')
        info = prefix.stat()
        directory = temporary / ('.wine-' + str(os.getuid())) / f'server-{info.st_dev:x}-{info.st_ino:x}'
        directory.mkdir(parents=True)
        lock = directory / 'lock'
        lock.write_text('')
        self.assertEqual(gate.server_lock_state(prefix, temporary)['state'], 'unlocked')
        # A separate Python process holds the same byte-range lock Wine uses.
        # This fixture executes neither Wine nor a native compiler.
        script = ('import fcntl,sys; f=open(sys.argv[1],"r+"); '
                  'fcntl.lockf(f,fcntl.LOCK_EX,1,0); print("ready",flush=True); sys.stdin.readline()')
        process = subprocess.Popen([sys.executable, '-c', script, str(lock)], text=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with self.assertRaises(BlockingIOError):
                gate.server_lock_state(prefix, temporary)
            with mock.patch.object(gate, 'server_lock_state', side_effect=BlockingIOError('held')), \
                 mock.patch.object(gate, 'command', side_effect=[0, 0, 1]):
                self.work.mkdir()
                # Absence-probe diagnostics are independently required empty.
                (self.work / 'outer-server-absent.log').write_text('')
                record = gate.cleanup_server(self.server, {'WINEPREFIX': str(prefix)}, self.work)
                self.assertEqual(record['status'], 'failed')
                self.assertIn('held', record['lock']['error'])
        finally:
            process.communicate('\n', timeout=5)
        self.assertEqual(gate.server_lock_state(prefix, temporary)['state'], 'unlocked')
        lock.unlink()
        lock.symlink_to(self.root / 'external-lock')
        with self.assertRaises(OSError):
            gate.server_lock_state(prefix, temporary)
        lock.unlink()
        os.mkfifo(lock)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            gate.server_lock_state(prefix, temporary)

    def test_command_nonzero_is_accepted_only_when_explicit(self):
        log = self.root / 'command.log'
        with mock.patch.object(gate.subprocess, 'run', return_value=mock.Mock(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'command failed'):
                gate.command(['/not-executed'], {}, log, 1)
            self.assertEqual(gate.command(['/not-executed'], {}, log, 1, accepted=(0, 1)), 1)

    def test_source_and_provenance_admission_precede_execution(self):
        with mock.patch.object(gate, 'admit_cpu_devices'), \
             mock.patch.object(gate, 'admit_source', side_effect=ValueError('pin mismatch')), \
             mock.patch.object(gate, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'pin mismatch'):
                gate.run(self.source, self.tool, self.work)
        command.assert_not_called()
        self.assertEqual(json.loads((self.work / 'INTEGRATION.json').read_text())['status'], 'failed')
        provenance = self.tool / 'provenance/PROVENANCE.json'
        provenance.parent.mkdir()
        record = {'proton': 'fixture-head', 'input_lock_sha256': gate.sha256(gate.HERE / 'inputs.lock.json'),
                  'components': {'zluda': gate.LOCK['components']['zluda']}}
        provenance.write_text(json.dumps(record))
        for binary in [self.wine, self.server]:
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text('fixture, never executed')
            binary.chmod(0o755)
        with mock.patch.object(gate, 'git', return_value='fixture-head'), \
             mock.patch.object(gate.cuda_package, 'audit') as audit:
            self.assertEqual(gate.admit_tool(self.source, self.tool), [self.wine, self.server])
            audit.assert_called_once_with(self.tool)
            self.wine.unlink()
            self.wine.symlink_to('/bin/true')
            with self.assertRaisesRegex(ValueError, 'non-tool'):
                gate.admit_tool(self.source, self.tool)
            record['components']['zluda'] = 'wrong'
            provenance.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'provenance'):
                gate.admit_tool(self.source, self.tool)

    def test_source_gate_checks_parent_child_tree_lock_and_dirty_files(self):
        bridge = gate.LOCK['wine_bridge']
        pin = gate.LOCK['components']['zluda']
        for mismatch in [None, 'parent', 'child', 'tree', 'dirty', 'lock']:
            with self.subTest(mismatch=mismatch):
                def git(root, *args):
                    if args[0] == 'status':
                        return ' M tests/abi/probe.c' if mismatch == 'dirty' else ''
                    field = 'parent' if root == self.source else 'child' if args[-1] == 'HEAD' else 'tree'
                    return 'wrong' if field == mismatch else bridge['tree'] if field == 'tree' else pin
                with mock.patch.object(gate, 'git', side_effect=git), \
                     mock.patch.object(gate, 'sha256', return_value='wrong' if mismatch == 'lock'
                                       else bridge['cargo_lock_sha256']):
                    if mismatch:
                        with self.assertRaises(ValueError):
                            gate.admit_source(self.source)
                    else:
                        self.assertEqual(gate.admit_source(self.source),
                                         self.source / 'zluda' / bridge['path'] / 'tests/abi')


if __name__ == '__main__':
    unittest.main()
