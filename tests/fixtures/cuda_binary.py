"""Small parser fixtures. These contain no code and are never loaded/executed."""
import struct


def pe(machine, exports=0, imports=(), delay_imports=()):
    data = bytearray(4096)
    data[:2] = b'MZ'
    data[0x40:0x50] = b'Wine builtin DLL'
    struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    size = 240 if machine == 0x8664 else 224
    struct.pack_into('<HHIIIHH', data, 132, machine, 1, 0, 0, 0, size, 0)
    struct.pack_into('<H', data, 152, 0x20b if machine == 0x8664 else 0x10b)
    directory = 152 + (112 if machine == 0x8664 else 96)
    struct.pack_into('<I', data, directory - 4, 16)
    struct.pack_into('<II', data, directory, 0x1000 if exports else 0, 40 if exports else 0)
    struct.pack_into('<8sIIII', data, 152 + size, b'.edata', 3584, 0x1000, 3584, 512)
    struct.pack_into('<I', data, 536, exports)
    names_offset = 2048
    for index, start, stride, names in [(1, 640, 20, imports), (13, 1152, 32, delay_imports)]:
        if not names:
            continue
        struct.pack_into('<II', data, directory + index * 8, 0x1000 + start - 512, (len(names) + 1) * stride)
        for position, name in enumerate(names):
            name_rva = 0x1000 + names_offset - 512
            if index == 1:
                struct.pack_into('<IIIII', data, start + stride * position, 0, 0, 0, name_rva, 0)
            else:
                struct.pack_into('<IIIIIIII', data, start + stride * position, 1, name_rva, 0, 0, 0, 0, 0, 0)
            value = name.encode('ascii') + b'\0'
            data[names_offset:names_offset + len(value)] = value
            names_offset += len(value)
    return bytes(data)


def elf(needed=('libc.so.6',), wow64=True, functions=(), soname=None, eager=True):
    names = ['__wine_unix_call_funcs'] + (['__wine_unix_call_wow64_funcs'] if wow64 else []) + list(functions)
    strings = b'\0'
    offsets = {}
    for name in [*names, *needed, *([soname] if soname else [])]:
        offsets[name] = len(strings)
        strings += name.encode() + b'\0'
    symbols = bytes(24) + b''.join(struct.pack('<IBBHQQ', offsets[name], 0x12 if name in functions else 0x11, 0, 1, 4096, 8) for name in names)
    dynamic = b''.join(struct.pack('<qQ', 1, offsets[name]) for name in needed)
    if soname:
        dynamic += struct.pack('<qQ', 14, offsets[soname])
    if eager:
        dynamic += struct.pack('<qQ', 24, 0)
    dynamic += bytes(16)
    data = bytearray(64 + 4 * 64)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', data, 16, 3)
    struct.pack_into('<H', data, 18, 62)
    struct.pack_into('<Q', data, 40, 64)
    struct.pack_into('<HH', data, 58, 64, 4)
    for index, kind, payload, link in [(1, 3, strings, 0), (2, 11, symbols, 1), (3, 6, dynamic, 1)]:
        struct.pack_into('<IIQQQQIIQQ', data, 64 + index * 64, 0, kind, 0, 0, len(data), len(payload), link, 0, 1, 0)
        data.extend(payload)
    return bytes(data)
