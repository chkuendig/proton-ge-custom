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
    def test_reuse_rejects_build_changes_and_preserves_nonassembly_functions(self):
        prefix = reuse.PREFIX
        for name in [prefix + 'build-bridges.sh', 'zluda', 'Makefile.in']:
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

    def test_reuse_lock_exception_only_covers_separately_rebuilt_nvapi(self):
        old = {'components': {'zluda':'same'}, 'sdk_image':'same', 'component_patches': {
            'dxvk-nvapi': {'prepared_tree':'old'}, 'protonfixes': {'prepared_tree':'same'}}}
        new = copy.deepcopy(old)
        new['component_patches']['dxvk-nvapi']['prepared_tree'] = 'new'
        reuse.validate_lock(json.dumps(old), json.dumps(new))
        for mutate in [lambda v:v['components'].update(zluda='changed'),
                       lambda v:v.update(sdk_image='changed'),
                       lambda v:v['component_patches']['protonfixes'].update(prepared_tree='changed')]:
            bad = copy.deepcopy(new)
            mutate(bad)
            with self.assertRaisesRegex(ValueError, 'non-NVAPI'):
                reuse.validate_lock(json.dumps(old), json.dumps(bad))
