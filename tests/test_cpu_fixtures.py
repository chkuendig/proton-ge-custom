"""Checksum/provenance admission of SDK-built diagnostic probes, no compiler."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import cpu_fixtures as gate
from inputs import sha256


class CpuFixtures(unittest.TestCase):
    def fixture(self, root):
        source, build, sdk, dest = [root/name for name in ['source','build','sdk.json','package']]
        path=source/'zluda'/gate.LOCK['wine_bridge']['path']/'tests/abi/fixture.c'
        path.parent.mkdir(parents=True);path.write_text('pinned fixture source')
        build.mkdir()
        for name in gate.REQUIRED:
            (build/name).write_text('compiled diagnostic '+name)
        (build/'SOURCE-SHA256SUMS').write_text(sha256(path)+'  fixture.c\n')
        sdk.write_text('{"profile":"generated","sha256":{"header":"receipt"}}')
        return source,build,sdk,dest,path

    def test_sdk_archive_admits_only_same_checkout_lock_source_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(gate,'git',return_value='a'*40), \
                mock.patch.object(gate.reuse_components, 'expected_commit', return_value='a'*40):
            root=Path(temporary)
            source,build,sdk,dest,path=self.fixture(root)
            record=gate.package(source,build,sdk,dest)
            self.assertEqual(gate.admit(source,dest,root/'extracted'),record)
            for name in gate.REQUIRED:
                self.assertEqual((root/'extracted'/name).read_bytes(),(build/name).read_bytes())
            meta=dest/'cpu-fixtures.json'
            for key,value in [('source_pin','wrong'),('sdk_image','wrong'),('proton','wrong'),
                              ('input_lock_sha256','wrong'),('archive_sha256','wrong')]:
                altered=dict(record);altered[key]=value;meta.write_text(json.dumps(altered))
                with self.assertRaisesRegex(ValueError,'provenance/archive mismatch'):
                    gate.admit(source,dest,root/('bad-'+key))
            meta.write_text(json.dumps(record));path.write_text('changed source')
            with self.assertRaisesRegex(ValueError,'source differs'):
                gate.admit(source,dest,root/'bad-source')

    def test_missing_source_inventory_and_unsafe_archive_member_fail(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(gate,'git',return_value='a'*40), \
                mock.patch.object(gate.reuse_components, 'expected_commit', return_value='a'*40):
            root=Path(temporary)
            source,build,sdk,dest,path=self.fixture(root)
            saved=(build/'SOURCE-SHA256SUMS').read_text()
            (build/'SOURCE-SHA256SUMS').write_text('')
            with self.assertRaisesRegex(ValueError,'inventory is empty'):
                gate.package(source,build,sdk,dest)
            (build/'SOURCE-SHA256SUMS').write_text(saved)
            record=gate.package(source,build,sdk,dest)
            archive=dest/'cpu-fixtures.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                member=tarfile.TarInfo('../outside');member.size=1;tar.addfile(member,io.BytesIO(b'x'))
            record['archive_sha256']=sha256(archive)
            (dest/'cpu-fixtures.json').write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError,'unsafe member'):
                gate.admit(source,dest,root/'unsafe')
            self.assertFalse((root/'outside').exists())
