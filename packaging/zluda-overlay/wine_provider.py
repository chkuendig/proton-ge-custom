#!/usr/bin/env python3
"""Inspect the matching Wine callback provider without loading or executing it."""
import argparse
import json
from pathlib import Path
import re
import struct
import sys


def elf_details(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 62:
        raise ValueError('Wine Unix module must be ELF64 x86_64')
    start, = struct.unpack_from('<Q', data, 40)
    stride, count = struct.unpack_from('<HH', data, 58)
    if stride != 64 or start + count * stride > len(data):
        raise ValueError('invalid ELF section table')
    sections = [struct.unpack_from('<IIQQQQIIQQ', data, start + i * stride) for i in range(count)]
    def content(section):
        position, size = section[4:6]
        if position + size > len(data):
            raise ValueError('ELF section outside file')
        return data[position:position + size]
    def string(table, offset):
        return table[offset:table.index(b'\0', offset)].decode()
    symbols, functions, needed, sonames = set(), set(), [], []
    for section in sections:
        if section[1] == 11:  # SHT_DYNSYM
            table = content(sections[section[6]])
            for record in struct.iter_unpack('<IBBHQQ', content(section)):
                name, info, visibility, index, _, _ = record
                if index and info >> 4 in (1, 2) and visibility & 3 == 0:
                    symbols.add(string(table, name))
                    if info & 15 == 2:
                        functions.add(string(table, name))
        if section[1] == 6:  # SHT_DYNAMIC
            table = content(sections[section[6]])
            entries = list(struct.iter_unpack('<qQ', content(section)))
            needed += [string(table, value) for tag, value in entries if tag == 1]
            sonames += [string(table, value) for tag, value in entries if tag == 14]
    return {'symbols': symbols, 'functions': functions, 'needed': needed, 'sonames': sonames,
            'type': struct.unpack_from('<H', data, 16)[0]}


def inspect(path):
    info = elf_details(path)
    if info['type'] != 3 or info['sonames'] != ['ntdll.so'] or 'KeUserModeCallback' not in info['functions']:
        raise ValueError('Wine ntdll provider must export KeUserModeCallback with SONAME ntdll.so: ' + str(path))
    return {'soname': 'ntdll.so', 'callback': 'KeUserModeCallback', 'needed': info['needed']}


def tool_provider(tool):
    path = tool / 'files/lib/wine/x86_64-unix/ntdll.so'
    if not path.is_file() or not path.resolve().is_relative_to(tool.resolve()):
        raise ValueError('Wine callback provider missing or outside assembled tool: ' + str(path))
    inspect(path)
    return path


def check_resolution(tool, report):
    expected = tool_provider(tool).resolve()
    rows = [line.strip() for line in report.splitlines() if re.match(r'\s*ntdll\.so(?:\s|$)', line)]
    match = re.fullmatch(r'ntdll\.so\s+=>\s+(/.+?)\s+\(0x[0-9a-f]+\)', rows[0]) if len(rows) == 1 else None
    if match is None or Path(match[1]).resolve() != expected:
        raise ValueError('bridge ntdll.so did not resolve to the assembled Wine provider: ' + repr(rows))
    return {'provider': str(expected), 'soname': 'ntdll.so'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--tool', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check_resolution(args.tool, sys.stdin.read()), sort_keys=True))
