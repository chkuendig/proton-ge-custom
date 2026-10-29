"""Small parser fixtures. These contain no code and are never loaded/executed."""
import struct


def pe(machine, exports=0):
    data = bytearray(1024)
    data[:2] = b'MZ'
    data[0x40:0x50] = b'Wine builtin DLL'
    struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    size = 240 if machine == 0x8664 else 224
    struct.pack_into('<HHIIIHH', data, 132, machine, 1, 0, 0, 0, size, 0)
    struct.pack_into('<H', data, 152, 0x20b if machine == 0x8664 else 0x10b)
    directory = 152 + (112 if machine == 0x8664 else 96)
    struct.pack_into('<II', data, directory, 0x1000 if exports else 0, 40 if exports else 0)
    struct.pack_into('<8sIIII', data, 152 + size, b'.edata', 512, 0x1000, 512, 512)
    struct.pack_into('<I', data, 536, exports)
    return bytes(data)


def elf(needed=('libc.so.6',), wow64=True):
    names = ['__wine_unix_call_funcs'] + (['__wine_unix_call_wow64_funcs'] if wow64 else [])
    strings = b'\0'
    offsets = {}
    for name in [*names, *needed]:
        offsets[name] = len(strings)
        strings += name.encode() + b'\0'
    symbols = bytes(24) + b''.join(struct.pack('<IBBHQQ', offsets[name], 0x11, 0, 1, 4096, 8) for name in names)
    dynamic = b''.join(struct.pack('<qQ', 1, offsets[name]) for name in needed) + bytes(16)
    data = bytearray(64 + 4 * 64)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', data, 18, 62)
    struct.pack_into('<Q', data, 40, 64)
    struct.pack_into('<HH', data, 58, 64, 4)
    for index, kind, payload, link in [(1, 3, strings, 0), (2, 11, symbols, 1), (3, 6, dynamic, 1)]:
        struct.pack_into('<IIQQQQIIQQ', data, 64 + index * 64, 0, kind, 0, 0, len(data), len(payload), link, 0, 1, 0)
        data.extend(payload)
    return bytes(data)
