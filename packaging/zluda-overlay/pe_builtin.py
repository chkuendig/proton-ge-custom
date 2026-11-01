#!/usr/bin/env python3
"""Mark a PE image as a Wine builtin, as `winebuild --builtin` does.

ntdll only loads a module from lib/wine/<arch>-windows when the DOS stub
carries this signature. The zluda part has no Wine SDK, so write it here.
"""
import struct
import sys

# Wine compares sizeof("Wine builtin DLL"), so the terminating NUL is part
# of the signature (winebuild writes all 17 bytes).
SIGNATURE = b'Wine builtin DLL\0'
OFFSET = 0x40


def mark(path):
    with open(path, 'r+b') as stream:
        header = stream.read(OFFSET + len(SIGNATURE))
        if header[:2] != b'MZ':
            raise ValueError('not a PE image: ' + path)
        (lfanew,) = struct.unpack_from('<I', header, 0x3c)
        if lfanew < OFFSET + len(SIGNATURE):
            raise ValueError('DOS stub too small for the builtin signature: ' + path)
        stream.seek(lfanew)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('missing PE signature: ' + path)
        stream.seek(OFFSET)
        stream.write(SIGNATURE)


if __name__ == '__main__':
    for name in sys.argv[1:]:
        mark(name)
