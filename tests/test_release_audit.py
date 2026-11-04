"""The artifact audit must reject lost stock notices and missing modified source."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/zluda-overlay'))
import release_audit as audit


class SourcesAndNotices(unittest.TestCase):
    def inventories(self):
        file = {'kind': 'file', 'size': 17, 'sha256': 'a' * 64, 'mtime': 100}
        stock = {p: dict(file) for p in ['LICENSE', 'LICENSE.OFL', 'PATENTS.AV1']}
        candidate = {**deepcopy(stock), **{p: dict(file) for p in audit.SOURCE_AND_NOTICE_REQUIRED}}
        return {'candidate': candidate, 'Valve': stock,
                'A1': {'LICENSE': dict(file)}, 'B5': {'LICENSE': dict(file),
                'sources/wine-sources.tar.gz': dict(file)}}

    def test_complete_inventory_records_base_source_replacement(self):
        result = audit.audit_sources_and_notices(self.inventories())
        self.assertIn('sources/wine-sources.tar.gz', result['comparisons']['sources']['B5'])
        self.assertIn('sources/valve-proton-sources.tar.gz', result['sources']['candidate'])
        self.assertEqual(result['notices']['Valve']['LICENSE']['sha256'], 'a' * 64)

    def test_lost_or_modified_stock_notice_fails(self):
        for name in ['LICENSE', 'LICENSE.OFL', 'PATENTS.AV1']:
            for changed in [False, True]:
                trees = self.inventories()
                if changed:
                    trees['candidate'][name]['sha256'] = 'b' * 64
                else:
                    del trees['candidate'][name]
                with self.assertRaisesRegex(ValueError, 'stock notice lost or changed'):
                    audit.audit_sources_and_notices(trees)

    def test_each_missing_or_empty_required_source_and_notice_fails(self):
        for name in audit.SOURCE_AND_NOTICE_REQUIRED:
            for empty in [False, True]:
                trees = self.inventories()
                if empty:
                    trees['candidate'][name]['size'] = 0
                else:
                    del trees['candidate'][name]
                with self.assertRaisesRegex(ValueError, 'required source or notice missing'):
                    audit.audit_sources_and_notices(trees)
