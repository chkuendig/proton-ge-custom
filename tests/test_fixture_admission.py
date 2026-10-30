"""Canonical GE stubs and compile-input reuse must fail closed."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/zluda-overlay'))
import pinned_fixture_runner as adapter
import reuse_components as reuse


class Admission(unittest.TestCase):
    def test_only_two_exact_regular_stubs_are_admitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            allowed = {}
            for arch in ['x86_64-windows', 'i386-windows']:
                path = root / arch / 'nvcuda.dll'
                path.parent.mkdir()
                path.write_bytes(arch.encode())
                allowed[arch] = hashlib.sha256(path.read_bytes()).hexdigest()
            with mock.patch.object(adapter.cuda_package.policy, 'STUB_SHA256', allowed):
                adapter.require_ge_stubs(root)
                for name in ['x86_64-unix/nvcuda.so', 'i386-unix/nvcuda.dll.so',
                             'x86_64-unix/libcuda.so.1']:
                    path = root / name
                    path.parent.mkdir(exist_ok=True)
                    path.symlink_to('/absent')
                    with self.assertRaises(ValueError):
                        adapter.require_ge_stubs(root)
                    path.unlink()
                path = root / 'i386-windows/nvcuda.dll'
                data = path.read_bytes()
                path.write_bytes(b'foreign module')
                with self.assertRaises(ValueError):
                    adapter.require_ge_stubs(root)
                path.unlink()
                with self.assertRaises(ValueError):
                    adapter.require_ge_stubs(root)
                target = root / 'original'
                target.write_bytes(data)
                path.symlink_to(target)
                with self.assertRaises(ValueError):
                    adapter.require_ge_stubs(root)

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
