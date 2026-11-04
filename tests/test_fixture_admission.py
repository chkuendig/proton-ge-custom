"""Canonical GE stubs and compile-input reuse must fail closed."""
from pathlib import Path
import copy
import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/zluda-overlay'))
import reuse_components as reuse


class Admission(unittest.TestCase):
    def test_bridge_reuse_allows_only_backend_and_control_lock_changes(self):
        before = {'components': {'zluda': 'old-native', 'wine-bridge': 'same-bridge', 'wine': 'same-wine'},
                  'sdk_image': 'same-sdk', 'llvm': 'same-llvm', 'comparison_control': {'components_run': 1}}
        after = copy.deepcopy(before)
        after['components']['zluda'] = 'published-native'
        after['comparison_control'] = {'components_run': 2, 'archive_sha256': 'published'}
        def admit(candidate):
            def git(source, *args):
                if args[0] == 'diff':
                    return 'zluda\n' + reuse.PREFIX + 'inputs.lock.json'
                return json.dumps(candidate if args[1].startswith('HEAD:') else before)
            with mock.patch.object(reuse.subprocess, 'run'), mock.patch.object(reuse, 'git', side_effect=git):
                return reuse.validate_changes(Path('/source'), 'a' * 40, 'bridges')
        self.assertEqual(admit(after), 'a' * 40)
        for key in ['wine-bridge', 'wine']:
            altered = copy.deepcopy(after)
            altered['components'][key] = 'different'
            with self.assertRaisesRegex(ValueError, 'build input'):
                admit(altered)
        for key in ['sdk_image', 'llvm']:
            altered = copy.deepcopy(after)
            altered[key] = 'different'
            with self.assertRaisesRegex(ValueError, 'build input'):
                admit(altered)

    def test_bridge_receipt_excludes_native_and_never_drops_fixture_artifacts(self):
        run = {'repository': {'full_name': 'chkuendig/proton-ge-custom'},
               'head_branch': 'valve/11.0-zluda-20261104', 'head_sha': 'a' * 40, 'id': 123}
        artifacts = {'artifacts': [dict(id=i, name=name, expired=False,
                                       digest='sha256:' + 'b' * 64, size_in_bytes=17)
                                  for i, name in enumerate(['overlay-zluda', 'overlay-bridges',
                                                            'wine-cpu-fixtures', 'w1-fixtures'])]}
        with mock.patch.object(reuse, 'validate_changes', return_value='a' * 40):
            result = reuse.receipt(Path('/source'), run, artifacts, 'bridges')
            self.assertEqual(result['part'], 'bridges')
            self.assertNotIn('overlay-zluda', {a['name'] for a in result['artifacts']})
            for name in ['overlay-bridges', 'wine-cpu-fixtures', 'w1-fixtures']:
                missing = {'artifacts': [a for a in artifacts['artifacts'] if a['name'] != name]}
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    reuse.receipt(Path('/source'), run, missing, 'bridges')

    def test_valve_reuse_requires_all_four_artifacts_and_exact_branch(self):
        run = {'repository': {'full_name': 'chkuendig/proton-ge-custom'},
               'head_branch': 'valve/11.0-zluda-20261104', 'head_sha': 'a' * 40, 'id': 123}
        artifacts = {'artifacts': [dict(id=i, name=name, expired=False,
                                       digest='sha256:' + 'b' * 64, size_in_bytes=17)
                                  for i, name in enumerate(['overlay-zluda', 'overlay-bridges',
                                                            'wine-cpu-fixtures', 'w1-fixtures'])]}
        with mock.patch.object(reuse, 'validate_changes', return_value='a' * 40):
            self.assertEqual(len(reuse.receipt(Path('/source'), run, artifacts)['artifacts']), 4)
            for index in range(4):
                missing = {'artifacts': [a for i, a in enumerate(artifacts['artifacts']) if i != index]}
                with self.assertRaisesRegex(ValueError, 'incomplete'):
                    reuse.receipt(Path('/source'), run, missing)
            run['head_branch'] = 'bridge/nvcuda-guard-20261030'
            with self.assertRaisesRegex(ValueError, 'source branch'):
                reuse.receipt(Path('/source'), run, artifacts)

    def test_reuse_rejects_build_changes_and_preserves_nonassembly_functions(self):
        prefix = reuse.PREFIX
        for name in [prefix + 'build-bridges.sh', prefix + 'inputs.lock.json', 'zluda', 'Makefile.in']:
            with mock.patch.object(reuse.subprocess, 'run'), \
                    mock.patch.object(reuse, 'git', return_value=name):
                with self.assertRaisesRegex(ValueError, 'build input'):
                    reuse.validate_changes(Path('/source'), 'a'*40)
        original = 'import os\ndef sdk():\n return 1\ndef assemble(x):\n return 1\n'
        admitted = original.replace('return 1\n', 'return 2\n', 1)
        self.assertNotEqual(reuse.admission_ast(original, {'assemble'}),
                            reuse.admission_ast(admitted, {'assemble'}))
        changed = original + 'import reuse_components\n'
        changed = changed.rsplit('return 1', 1)
        changed = 'return 2'.join(changed)
        self.assertEqual(reuse.admission_ast(original, {'assemble'}),
                         reuse.admission_ast(changed, {'assemble'}))
