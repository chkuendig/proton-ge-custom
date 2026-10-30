"""Inspect fixture ELF bytes; never compile or load a shared library."""
from pathlib import Path
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from cuda_binary import elf
import wine_provider as gate


class WineProvider(unittest.TestCase):
    def test_exact_native_provider_identity_and_defined_callback_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ntdll.so'
            valid = elf(functions=['KeUserModeCallback'], soname='ntdll.so')
            path.write_bytes(valid)
            self.assertEqual(gate.inspect(path)['soname'], 'ntdll.so')
            for field,value in [(16, 2), (18, 3)]:
                data=bytearray(valid);struct.pack_into('<H',data,field,value);path.write_bytes(data)
                with self.assertRaises(ValueError):
                    gate.inspect(path)
            # An undefined import with the same name is not a provider.
            data=bytearray(valid)
            symbols=struct.unpack_from('<Q',data,64+2*64+24)[0]
            struct.pack_into('<H',data,symbols+3*24+6,0)
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'must export KeUserModeCallback'):
                gate.inspect(path)
            for data in [elf(soname='ntdll.so'),elf(functions=['KeUserModeCallback'],soname='/build/ntdll.so')]:
                path.write_bytes(data)
                with self.assertRaises(ValueError):
                    gate.inspect(path)

    def test_dependency_resolves_only_to_this_tool_and_never_an_sdk_or_system_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            tool=Path(temporary)/'tool'
            path=tool/'files/lib/wine/x86_64-unix/ntdll.so'
            path.parent.mkdir(parents=True)
            path.write_bytes(elf(functions=['KeUserModeCallback'],soname='ntdll.so'))
            valid='ntdll.so => '+str(path)+' (0x1234)\nlibc.so.6 => /sdk/libc.so.6 (0x5678)\n'
            self.assertEqual(gate.check_resolution(tool,valid)['provider'],str(path))
            for text in ['',valid+valid,valid.replace(str(path),'/sdk/ntdll.so'),
                         'ntdll.so => not found\n','ntdll.so (0x1234)\n']:
                with self.assertRaises(ValueError):
                    gate.check_resolution(tool,text)
            outside=tool.parent/'ntdll.so';outside.write_bytes(path.read_bytes())
            path.unlink();path.symlink_to(outside)
            with self.assertRaisesRegex(ValueError,'outside assembled tool'):
                gate.check_resolution(tool,valid)
