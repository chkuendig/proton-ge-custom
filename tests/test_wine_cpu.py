"""Hosted CPU-gate orchestration fixtures; no Wine or compiler execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import wine_cpu as gate


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

    def results(self, directory):
        directory.mkdir(parents=True)
        record = {'qualification': gate.PASS,
                  'cases': [{'mode': mode, 'returncode': 0} for mode in gate.MODES],
                  'cleanup': {'returncode': 0}}
        (directory / 'RESULT.json').write_text(json.dumps(record))
        for name in ['bootstrap', *gate.MODES, 'wineserver-cleanup']:
            (directory / (name + '.log')).write_text('fixture log\n')
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
                         for arch in ['x86_64-linux-gnu', 'i386-linux-gnu']))
        self.assertEqual(env['WINEPREFIX'], str(self.work / 'prefix'))
        self.assertEqual(env['WINESERVER'], str(self.server))
        self.assertEqual(env['WINEARCH'], 'win64')

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
        backend.write_bytes(b'\x7fELF\x02\x01fixture')
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
                        dict(valid, cases=list(reversed(valid['cases']))),
                        dict(valid, cleanup={'returncode': 1}),
                        dict(valid, cases=[dict(case, returncode=1) for case in valid['cases']])]:
            (results / 'RESULT.json').write_text(json.dumps(invalid))
            with self.assertRaises(ValueError):
                gate.check_result(results)
        (results / 'RESULT.json').write_text(json.dumps(valid))
        (results / 'off-again.log').unlink()
        with self.assertRaisesRegex(ValueError, 'log missing'):
            gate.check_result(results)

    def exercise(self, mode='pass'):
        commands = []

        def command(argv, env, log, timeout):
            argv = [str(value) for value in argv]
            commands.append((argv, env, timeout))
            log.write_text('orchestration fixture\n')
            if 'run-fixtures.py' in argv[1]:
                (self.work / 'prefix').mkdir()
                if mode == 'timeout':
                    raise subprocess.TimeoutExpired(argv, timeout)
                if mode != 'missing-result':
                    self.results(self.work / 'results')
            if mode == 'kill-failure' and argv[-1] == '-k':
                raise RuntimeError('fixture kill failure')

        with mock.patch.object(gate, 'admit_cpu_devices'), \
             mock.patch.object(gate, 'admit_source', return_value=self.source / 'tests/abi'), \
             mock.patch.object(gate, 'admit_tool', return_value=[self.wine, self.server]), \
             mock.patch.object(gate, 'inspect_backend', return_value=['libc.so.6']), \
             mock.patch.object(gate, 'command', side_effect=command):
            if mode == 'pass':
                record = gate.run(self.source, self.tool, self.work)
                self.assertEqual(record['status'], 'passed')
                self.assertEqual(record['qualification'], gate.PASS)
            else:
                with self.assertRaises((RuntimeError, subprocess.TimeoutExpired, FileNotFoundError)):
                    gate.run(self.source, self.tool, self.work)
        record = json.loads((self.work / 'INTEGRATION.json').read_text())
        self.assertEqual(record['status'], 'passed' if mode == 'pass' else 'failed')
        # Both cleanup calls must run even after probe timeout, absent result,
        # or a failure of the first cleanup call.
        self.assertEqual([entry[0] for entry in commands[-2:]],
                         [[str(self.server), '-k'], [str(self.server), '-w']])
        self.assertTrue(all(env['WINEPREFIX'] == str(self.work / 'prefix')
                            for _, env, _ in commands))
        return commands

    def test_real_runner_contract_and_outer_cleanup(self):
        commands = self.exercise()
        argv = next(argv for argv, _, _ in commands if 'run-fixtures.py' in argv[1])
        options = dict(zip(argv[2::2], argv[3::2]))
        self.assertEqual(options, {'--wine': str(self.wine), '--wineserver': str(self.server),
            '--dll-dir': str(self.tool / 'files/lib/wine'),
            '--on-root': str(self.tool / 'files/lib/wine-cuda/zluda'),
            '--off-root': str(self.tool / 'files/lib/wine-cuda/stub'),
            '--backend': str(self.work / 'build/libcuda.so.1'),
            '--probe': str(self.work / 'build/probe.exe'), '--prefix': str(self.work / 'prefix'),
            '--results': str(self.work / 'results')})

    def test_probe_timeout_still_kills_and_waits(self):
        self.exercise('timeout')

    def test_runner_success_without_result_fails_closed(self):
        self.exercise('missing-result')

    def test_cleanup_failure_cannot_pass_and_still_waits(self):
        self.exercise('kill-failure')

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
