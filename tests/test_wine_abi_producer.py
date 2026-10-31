"""Reviewer mutations and notice extent, without a compiler or Wine execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import wine_abi
import overlay


class PreparedAbiReviewer(unittest.TestCase):
    def test_changed_body_fails_and_preserves_full_source_diagnostics(self):
        spec = importlib.util.spec_from_file_location('callback_reviewer', wine_abi.REVIEW / 'check_wine_callback.py')
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        original = 'void shutdown(void) { detach(); tls(); unlock(); }'
        tokens = check.tokens(original)
        contract = {'contract':'fixture', 'wine_commit':'fixture', 'callback_id':0,
                    'files':{'loader.c':{'fragments':[{'name':'ordered shutdown', 'begin':'void shutdown(void)',
                    'capture':'body', 'tokens':len(tokens),
                    'sha256':hashlib.sha256('\n'.join(tokens).encode()).hexdigest()}]}}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'loader.c'
            path.write_text(original)
            self.assertTrue(check.attest(root, contract)['attested'])
            path.write_text(original.replace('detach(); tls();', 'tls(); detach();'))
            report, artifacts = check.audit(root, contract)
            self.assertFalse(report['attested'])
            check.write_report(root / 'failure.json', report, artifacts)
            self.assertEqual((root / 'failure.fragments/source/loader.c').read_text(), path.read_text())

    def test_prepared_com_slot_order_is_checked_from_idl_bodies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'include').mkdir()
            path = root / 'include/interface.idl'
            text = '[uuid(abcdef)] interface Device : IUnknown {\nvoid first();\nvoid second();\n}'
            path.write_text(text)
            contract = {'source_files_sha256':{'interface.idl':'reviewed baseline'},
                        'interfaces':{'Device':{'uuid':'abcdef', 'vtable_words':5, 'methods':{'first':3,'second':4}}}}
            (root / 'd3d11-abi.json').write_text(json.dumps(contract))
            with mock.patch.object(wine_abi, 'REVIEW', root):
                self.assertIn('include/interface.idl', wine_abi.audit_com(root))
                path.write_text(text.replace('first();\nvoid second', 'second();\nvoid first'))
                with self.assertRaisesRegex(ValueError, 'COM slot changed'):
                    wine_abi.audit_com(root)

    def test_collects_complete_provenance_bundle_and_nested_test_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, dest = root / 'source', root / 'notices'
            for name in ['LICENSES/PROVENANCE.md', 'LICENSES/ABI-FACTS.json',
                         'LICENSES/ABI-FACTS.json.license', 'tests/lgpl/LICENSE.md']:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            found = overlay.collect_licenses(source, dest)
            self.assertEqual(set(found), {p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file()})
