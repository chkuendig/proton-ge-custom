"""No compiler/ELF execution: validate observed loader closure and exact copies."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import zoc_capsule as gate
from inputs import sha256, verify_manifest


class ZocCapsule(unittest.TestCase):
    def test_loader_output_is_closed_and_missing_or_duplicate_paths_fail(self):
        text = 'linux-vdso.so.1 (0x1234)\nlibc.so.6 => /sdk/libc.so.6 (0xabcd)\n/sdk/ld-linux-x86-64.so.2 (0x1234)\n'
        self.assertEqual(gate.loader_paths(text), {'libc.so.6':Path('/sdk/libc.so.6'),
                          'ld-linux-x86-64.so.2':Path('/sdk/ld-linux-x86-64.so.2')})
        for bad in ['libc.so.6 => not found', text + 'libc.so.6 => /other/libc.so.6 (0x9876)',
                    'unexpected loader diagnostic', '../libc.so.6 => /sdk/libc.so.6 (0x1234)']:
            with self.assertRaises(ValueError):
                gate.loader_paths(bad)

    def test_self_contained_capsule_uses_exact_sdk_bits_and_preserves_preload(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'; source.mkdir()
            for name in ['LICENSE-APACHE','LICENSE-MIT']:
                (source/name).write_text('source license fixture')
            (source/'Cargo.lock').write_text('pinned dependency fixture')
            paths = {name:root/name for name in ['zoc','libc.so.6','ld-linux-x86-64.so.2']}
            for file in paths.values():
                header = bytearray(64);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(62).to_bytes(2,'little')
                file.write_bytes(header + file.name.encode())
            stack = root/'stack-build'; stack.mkdir()
            stack_source = source/'wine/zluda_wine/tests/stack'; stack_source.mkdir(parents=True)
            for name in ['stack-probe.c','build-probe.sh','measure.py','README.md']:
                (stack_source/name).write_text('public measurement source '+name)
            (stack/'SOURCE-SHA256SUMS').write_text(''.join(
                sha256(path)+'  '+path.name+'\n' for path in sorted(stack_source.iterdir())))
            preload=stack/'libzluda_stack_probe.so'
            preload.write_bytes(paths['libc.so.6'].read_bytes())
            (stack/'TOOLCHAIN.txt').write_text('SDK compiler fixture')
            (stack/'SHA256SUMS').write_text(sha256(preload)+'  '+preload.name+'\n')
            def output(*argv):
                if argv[0]=='ldd':
                    return f'libc.so.6 => {paths["libc.so.6"]} (0xabcd)\n{paths["ld-linux-x86-64.so.2"]} (0x1234)\n'
                if argv[0]=='dpkg-query':
                    return 'libc6 2.99-fixture\n'
                if argv[2]=='--program-headers':
                    return f'[Requesting program interpreter: {paths["ld-linux-x86-64.so.2"]}]\n'
                if Path(argv[-1]).name == 'zoc':
                    return '(NEEDED) Shared library: [libc.so.6]\n'
                if Path(argv[-1]).name == 'libc.so.6':
                    return '(NEEDED) Shared library: [ld-linux-x86-64.so.2]\n'
                return ''
            def notices(src,dest,required):
                self.assertTrue(required);dest.mkdir(parents=True)
                (dest/'copyright').write_text('runtime license fixture')
            capsule=root/'capsule'
            with mock.patch.object(gate,'output',side_effect=output), \
                 mock.patch.object(gate,'package_owner',return_value='libc6:amd64'), \
                 mock.patch.object(gate,'collect_licenses',side_effect=notices), \
                 mock.patch.object(gate,'git',return_value='a'*40):
                record=gate.create(source,paths['zoc'],capsule,stack)
            self.assertEqual(record['interpreter'],'ld-linux-x86-64.so.2')
            self.assertEqual((capsule/'bin/zoc').read_bytes(),paths['zoc'].read_bytes())
            for name in ['libc.so.6','ld-linux-x86-64.so.2']:
                self.assertEqual((capsule/'lib'/name).read_bytes(),paths[name].read_bytes())
            wrapper=(capsule/'zoc').read_text()
            self.assertIn('LD_PRELOAD',wrapper)
            self.assertNotIn('env -i',wrapper)
            self.assertIn('--library-path "$capsule/lib"',wrapper)
            self.assertIn('NOT RUN',record['execution'])
            self.assertFalse(list(capsule.rglob('*.ptx')))
            self.assertEqual(record['stack_probe_sha256'],sha256(preload))
            for name in ['stack-probe.c','build-probe.sh','measure.py','README.md']:
                self.assertEqual((capsule/'stack'/name).read_bytes(),(stack_source/name).read_bytes())
            self.assertEqual((capsule/'stack/libzluda_stack_probe.so').read_bytes(),preload.read_bytes())
            verify_manifest(capsule)
            with mock.patch.object(gate,'output',return_value='(RPATH) Library rpath: [/work/sdk]'):
                with self.assertRaisesRegex(ValueError,'external ELF search path'):
                    gate.inspect(paths['zoc'])
