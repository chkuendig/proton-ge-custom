import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/zluda-overlay'))
import product_identity as gate


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as stream:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            stream.addfile(info, io.BytesIO(data))
    return output.getvalue()


class ProductIdentity(unittest.TestCase):
    def test_archive_parser_rejects_duplicate_and_parent_paths(self):
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            gate.inventory(io.BytesIO(archive({'../escape': b'x'})), False)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as out:
            for _ in range(2):
                out.addfile(tarfile.TarInfo('duplicate'))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            gate.inventory(io.BytesIO(stream.getvalue()), False)

    def test_changed_production_source_is_unclassified(self):
        before = archive({'wine/zluda_wine/src/bridge.rs': b'production'})
        after = archive({'wine/zluda_wine/src/bridge.rs': b'changed'})
        result = gate.nested_changes('sources/zluda-wine.tar.gz', before, after)
        self.assertEqual(result['unclassified'], ['wine/zluda_wine/src/bridge.rs'])

    def test_arbitrary_provenance_field_cannot_hide_runtime_change(self):
        name = 'provenance/PROVENANCE.json'
        self.assertIsNone(gate.metadata_change(name, b'{"arbitrary":1}', b'{"arbitrary":2}'))
        result = gate.metadata_change(name, b'{"proton":"old"}', b'{"proton":"new"}')
        self.assertEqual(result['changed_fields'], ['proton'])

    def test_removed_tool_must_be_in_reviewed_scope(self):
        source = 'wine/w1-fixtures/tools/cuda-count/count.c'
        result = gate.nested_changes('capsule', archive({source: b'old counter'}), archive({}))
        self.assertEqual(result['unclassified'], [])
        result = gate.nested_changes('capsule', archive({'unknown.c': b'input'}), archive({}))
        self.assertEqual(result['unclassified'], ['unknown.c'])
