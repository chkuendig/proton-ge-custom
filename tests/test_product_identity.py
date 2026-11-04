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

    def test_bridge_contract_allows_only_its_pinned_source_identity(self):
        name = 'provenance/ZLUDA-WINE.json'
        before = {'contract': {'source_commit': 'a'*40, 'modules': ['fixed']}, 'sha256': {'module': 'fixed'}}
        after = {'contract': dict(before['contract'], source_commit=gate.LOCK['components'][gate.bridge_component()]),
                 'sha256': before['sha256']}
        self.assertIsNotNone(gate.metadata_change(name, json.dumps(before).encode(), json.dumps(after).encode()))
        for changed in [dict(after, sha256={}), dict(after, contract=dict(after['contract'], modules=[]))]:
            self.assertIsNone(gate.metadata_change(name, json.dumps(before).encode(), json.dumps(changed).encode()))

    def test_removed_tool_must_be_in_reviewed_scope(self):
        source = 'wine/w1-fixtures/tools/cuda-count/count.c'
        result = gate.nested_changes('capsule', archive({source: b'old counter'}), archive({}))
        self.assertEqual(result['unclassified'], [])
        result = gate.nested_changes('capsule', archive({'unknown.c': b'input'}), archive({}))
        self.assertEqual(result['unclassified'], ['unknown.c'])

    def test_prepared_commit_metadata_cannot_change_sdk_or_source_hashes(self):
        before = {'prepared_commit': 'a'*40, 'sdk_files': {'lib/ntdll.so': 'fixed'},
                  'prepared_files': {'include/ntuser.h': 'fixed'}}
        after = dict(before, prepared_commit='b'*40)
        self.assertIsNotNone(gate.prepared_identity(json.dumps(before).encode(), json.dumps(after).encode()))
        for field in ['sdk_files', 'prepared_files']:
            changed = dict(after, **{field: {}})
            self.assertIsNone(gate.prepared_identity(json.dumps(before).encode(), json.dumps(changed).encode()))

    def test_recipe_directory_cannot_be_replaced_by_a_link(self):
        with mock.patch.object(gate, 'git', return_value='tree'):
            self.assertIsNotNone(gate.recipe_change('profiles', None, {'type': '5'}))
            self.assertIsNone(gate.recipe_change('profiles', None, {'type': '2', 'target': '/outside'}))

    def test_source_receipt_binds_every_external_member_and_actual_archive(self):
        abi = json.dumps({'prepared_commit': 'a'*40}).encode()
        capsule = archive({'external/prepared-wine-abi.json': abi, 'external/SDK': b'provider'})
        record = {'archive_sha256': gate.sha(capsule), 'compiled_source_closure': ['src/bridge.rs'],
                  'prepared_abi_receipt_sha256': gate.sha(abi),
                  'sdk_capsule': {'external/prepared-wine-abi.json': gate.sha(abi),
                                  'external/SDK': gate.sha(b'provider')}}
        before = json.dumps(dict(record, archive_sha256='old')).encode()
        contents = {'sources/zluda-wine.tar.gz': capsule}
        name = 'sources/zluda-wine.tar.gz.json'
        self.assertIsNotNone(gate.support_change(name, before, json.dumps(record).encode(), contents))
        for field, value in [('archive_sha256', 'forged'), ('sdk_capsule', {}),
                             ('compiled_source_closure', ['different.rs'])]:
            changed = dict(record, **{field: value})
            self.assertIsNone(gate.support_change(name, before, json.dumps(changed).encode(), contents))

    def test_build_log_allows_only_temporary_paths_and_elapsed_time(self):
        name = 'sources/zluda-wine.tar.gz.check.log'
        before = b'Compiling /work/archive-check-aaaa/wine\n    Finished dev in 8.64s\n'
        after = b'Compiling /work/archive-check-bbbb/wine\n    Finished dev in 6.46s\n'
        self.assertIsNotNone(gate.support_change(name, before, after, {}))
        self.assertIsNone(gate.support_change(name, before, after+b'error: broken\n', {}))

    def test_hardlinks_are_hashed_and_cycles_or_link_targets_are_rejected(self):
        def linked(target, kind=tarfile.REGTYPE):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as out:
                info = tarfile.TarInfo('target')
                info.type = kind
                if kind == tarfile.REGTYPE:
                    info.size = 7
                    out.addfile(info, io.BytesIO(b'runtime'))
                else:
                    info.linkname = 'alias'
                    out.addfile(info)
                link = tarfile.TarInfo('alias')
                link.type, link.linkname = tarfile.LNKTYPE, target
                out.addfile(link)
            return io.BytesIO(stream.getvalue())
        entries, _ = gate.inventory(linked('target'), False)
        self.assertEqual(entries['alias']['sha256'], gate.sha(b'runtime'))
        for stream in [linked('../target'), linked('missing'), linked('target', tarfile.LNKTYPE),
                       linked('target', tarfile.SYMTYPE)]:
            with self.assertRaises(ValueError):
                gate.inventory(stream, False)
