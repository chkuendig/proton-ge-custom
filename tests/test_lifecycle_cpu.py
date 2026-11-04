"""Lifecycle admission/control stand-ins; never invoke Wine or native tools."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import lifecycle_cpu as gate


class LifecycleCpu(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.expected = {'on': {'three': 'module-hashes'}, 'off': {'two': 'GE-hashes'},
                         'probe': 'probe-hash', 'helper': 'helper-hash', 'backend': 'fake-hash', 'transition': 'timing-probe-hash'}
        self.events = {mode: {mode + '-event': 1} for mode in gate.MODES}
        self.pe_events = {mode: [mode + '-PE-enter', mode + '-PE-leave'] for mode in gate.MODES}
        self.on_root, self.backend = self.root / 'tool/files/lib/wine-cuda/zluda', self.root / 'build/libcuda.so.1'

    def result(self, directory):
        directory.mkdir(parents=True)
        record = {'qualification': gate.QUALIFICATION, 'inputs': self.expected,
            'bootstrap': {'returncode': 0},
            'cases': [{'mode': mode, 'returncode': 0, 'log': mode + '.log',
                       'events': [mode + '-event'], 'pe_events': self.pe_events[mode],
                       'module_root': str(self.on_root), 'backend_path': str(self.backend)} for mode in gate.MODES],
            'cleanup': {'verified_absent': True, 'lock': {'absent': True},
                'steps': [{'command': ['/server', flag], 'returncode': code, 'accepted': True}
                          for flag, code in [('-k',1),('-w',0),('-k0',1)]]}}
        for mode in gate.MODES:
            (directory / (mode + '.log')).write_text('PASS: lifecycle fixture\n')
            (directory / (mode + '-backend.log')).write_text(mode + '-event\n')
            (directory / (mode + '-pe.log')).write_text('\n'.join(self.pe_events[mode]) + '\n')
        for name in ['bootstrap', 'wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']:
            (directory / (name + '.log')).write_text('')
        raw = {'iterations': 200000, 'frequency_hz': 10000000,
               'samples_ticks': [200000] * 11, 'baseline_ticks': [100000] * 11}
        record['unix_call_cost'] = dict(raw, median_ns_per_call=100, median_windows_baseline_ns=50,
            paired_delta_ns=[50] * 11, scope='hosted-CPU-pinned-Wine-noop; full-CUDA-wrapper-and-Deck-W1-W2-pending')
        (directory / 'unix-call-cost.log').write_text('TRANSITIONS: ' + json.dumps(raw) + '\nPASS: timing fixture\n')
        (directory / 'RESULT.json').write_text(json.dumps(record))
        return record

    def test_complete_receipt_passes_and_missing_or_forged_fields_fail(self):
        directory = self.root / 'results'
        valid = self.result(directory)
        self.assertEqual(gate.check_result(directory, self.expected, self.events, self.pe_events, self.on_root, self.backend), valid)
        changes = [lambda r:r.update(qualification='unqualified'), lambda r:r.update(inputs={}),
                   lambda r:r['bootstrap'].update(returncode=False),
                   lambda r:r['cases'].reverse(), lambda r:r['cases'][0].update(returncode=False),
                   lambda r:r['cases'][0].update(events=[]), lambda r:r['cases'][0]['pe_events'].reverse(),
                   lambda r:r['cases'][0].update(module_root='/wrong'),
                   lambda r:r['cases'][0].update(backend_path='/wrong'), lambda r:r['cases'][0].update(log='../other.log'),
                   lambda r:r['cleanup'].update(verified_absent=False),
                   lambda r:r['cleanup']['lock'].update(absent=False),
                   lambda r:r['cleanup']['steps'][2].update(returncode=0),
                   lambda r:r['cleanup']['steps'][1].update(returncode=False),
                   lambda r:r['unix_call_cost'].update(iterations=1),
                   lambda r:r['unix_call_cost'].update(median_ns_per_call=0),
                   lambda r:r['unix_call_cost'].update(paired_delta_ns=[])]
        for mutate in changes:
            record = copy.deepcopy(valid); mutate(record)
            (directory / 'RESULT.json').write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                gate.check_result(directory, self.expected, self.events, self.pe_events, self.on_root, self.backend)
        (directory / 'RESULT.json').write_text(json.dumps(valid))
        (directory / 'thread-exit-backend.log').write_text('unexpected-native-operation\n')
        with self.assertRaisesRegex(ValueError, 'events mismatch'):
            gate.check_result(directory, self.expected, self.events, self.pe_events, self.on_root, self.backend)
        (directory / 'thread-exit-backend.log').write_text('thread-exit-event\n')
        (directory / 'wineserver-verify.log').write_text('permission denied')
        with self.assertRaisesRegex(ValueError, 'absence diagnostics'):
            gate.check_result(directory, self.expected, self.events, self.pe_events, self.on_root, self.backend)

    def exercise(self, timeout=False):
        source, tool, build, work = [self.root / name for name in ['source','tool','build','work']]
        fixture = self.root / 'fixture'; fixture.mkdir()
        (fixture / 'run-lifecycle.py').write_text('EXPECTED_EVENTS=' + repr(self.events) + '\nEXPECTED_PE_EVENTS=' + repr(self.pe_events) + '\n')
        commands = []
        def command(argv, env, log, limit):
            commands.append(([str(a) for a in argv], env, limit))
            (work / 'prefix').mkdir()
            if timeout:
                raise TimeoutError('fixture lifecycle timeout')
            self.result(work / 'results')
            log.write_text('fixture runner')
        with mock.patch.object(gate.wine_cpu, 'admit_cpu_devices'), \
             mock.patch.object(gate.wine_cpu, 'admit_source', return_value=fixture), \
             mock.patch.object(gate.wine_cpu, 'admit_tool', return_value=[tool/'wine',tool/'wineserver']), \
             mock.patch.object(gate.wine_cpu, 'inspect_probe', return_value={'fixture':'PE64'}), \
             mock.patch.object(gate.wine_cpu, 'inspect_backend', return_value=['libc.so.6']), \
             mock.patch.object(gate, 'inputs', return_value=self.expected), \
             mock.patch.object(gate.wine_cpu, 'command', side_effect=command), \
             mock.patch.object(gate.wine_cpu, 'cleanup_server', return_value={'status':'passed'}) as cleanup:
            if timeout:
                with self.assertRaises(TimeoutError):
                    gate.run(source, tool, build, work)
            else:
                self.assertEqual(gate.run(source, tool, build, work)['status'], 'passed')
            cleanup.assert_called_once()
        self.assertEqual(commands[0][2], 600)
        self.assertIn(str(build/'lifecycle-helper.dll'), commands[0][0])
        self.assertEqual(commands[0][1]['WINEPREFIX'], str(work/'prefix'))
        self.assertEqual(json.loads((work/'INTEGRATION.json').read_text())['status'], 'failed' if timeout else 'passed')

    def test_fresh_lifecycle_prefix_and_exact_runner_arguments(self):
        self.exercise()

    def test_timeout_cannot_skip_outer_server_cleanup(self):
        self.exercise(timeout=True)
