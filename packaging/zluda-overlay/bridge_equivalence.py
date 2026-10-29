#!/usr/bin/env python3
"""Inspect baseline/relocated bridge bytes without executing either library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

MODULES = ['lib/wine/x86_64-windows/nvcuda.dll', 'lib/wine/i386-windows/nvcuda.dll',
           'lib/wine/x86_64-unix/nvcuda.dll.so', 'lib/wine/x86_64-unix/nvcuda.so']


def pe_exports(text, allow_empty=False):
    # Wine's --fake-module PE64 contains an empty export table. The callable
    # x64 table is embedded in nvcuda.dll.so, read by winelib_exports below.
    if allow_empty and re.search(r'Number in:\s+Export Address Table\s+0+\s+'
                                r'\[Name Pointer/Ordinal\] Table\s+0+(?:\s|$)', text):
        return []
    table = text.split('[Ordinal/Name Pointer] Table', 1)
    if len(table) != 2:
        raise ValueError('PE export name table missing')
    base = re.search(r'Ordinal Base\s+(\d+)', text)
    if base is None:
        raise ValueError('PE export ordinal base missing')
    exports = []
    for line in table[1].splitlines():
        match = re.fullmatch(r'\s*\[\s*(\d+)\]\s+(\S+)\s*', line)
        if match:
            exports.append((int(match[1]) + int(base[1]), match[2]))
        elif exports and not line.strip():
            break
    if not exports or len({name for _, name in exports}) != len(exports):
        raise ValueError('empty or duplicate PE exports')
    return sorted(exports)


def elf_exports(text):
    exports = []
    for line in text.splitlines():
        match = re.match(r'\s*\d+:\s+[0-9a-fA-F]+\s+(\d+)\s+(\S+)\s+'
                         r'(GLOBAL|WEAK)\s+(\S+)\s+(\S+)\s+(\S+)', line)
        if match and match[5] != 'UND':
            exports.append((match[6], int(match[1]), match[2], match[3], match[4]))
    if not exports:
        raise ValueError('no defined ELF dynamic exports')
    return sorted(exports)


def winelib_exports(data, symbols):
    """Read Wine's embedded PE export names; never dlopen the Winelib ELF.

    Pinned Wine tools/winebuild/spec32.c:output_module/output_exports and
    utils.c:output_rva use NT-header-relative RVAs, not normal PE file RVAs.
    """
    matches = re.findall(r'^\s*\d+:\s+([0-9a-fA-F]+)\s+\d+\s+\S+\s+\S+\s+\S+\s+'
                         r'\S+\s+__wine_spec_nt_header\s*$', symbols, re.M)
    if len(matches) != 1 or data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('missing unique Wine NT header in ELF64')
    base = int(matches[0], 16)
    start, = struct.unpack_from('<Q', data, 32)
    stride, count = struct.unpack_from('<HH', data, 54)
    if stride != 56 or not count:
        raise ValueError('unsupported ELF program table')
    segments = [struct.unpack_from('<IIQQQQQQ', data, start + i * stride) for i in range(count)]

    def offset(address, size=1):
        for kind, _, position, virtual, _, length, _, _ in segments:
            if kind == 1 and virtual <= address and address + size <= virtual + length:
                result = position + address - virtual
                if result + size <= len(data):
                    return result
        raise ValueError('Wine export address outside file-backed ELF segment')

    def rva_at(position):
        return base + struct.unpack_from('<i', data, position)[0]

    header = offset(base, 144)
    if data[header:header + 6] != b'PE\0\0\x64\x86' or data[header + 24:header + 26] != b'\x0b\x02':
        raise ValueError('expected embedded Wine x64 NT header')
    directory = offset(rva_at(header + 136), 40)
    first, functions, names = struct.unpack_from('<III', data, directory + 16)
    if not 0 < names <= functions <= 65536:
        raise ValueError('invalid Wine export counts')
    pointers = offset(rva_at(directory + 32), 4 * names)
    ordinals = offset(rva_at(directory + 36), 2 * names)
    exports = []
    for index in range(names):
        position = offset(rva_at(pointers + 4 * index))
        end = data.find(b'\0', position, position + 1024)
        if end < 0:
            raise ValueError('unterminated Wine export name')
        ordinal, = struct.unpack_from('<H', data, ordinals + 2 * index)
        if ordinal >= functions:
            raise ValueError('Wine export ordinal outside function table')
        exports.append((first + ordinal, data[position:end].decode('ascii')))
    if len({name for _, name in exports}) != names:
        raise ValueError('duplicate Wine export names')
    return sorted(exports)


def elf_sections(data):
    if data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('expected little-endian ELF64')
    offset, = struct.unpack_from('<Q', data, 40)
    stride, count, strings = struct.unpack_from('<HHH', data, 58)
    if stride != 64 or not count or not 0 < strings < count:
        raise ValueError('unsupported ELF section table')
    headers = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * stride) for i in range(count)]
    string_header = headers[strings]
    names = data[string_header[4]:string_header[4] + string_header[5]]
    sections = {}
    for name, kind, flags, _, start, size, _, _, alignment, _ in headers:
        label = names[name:names.find(b'\0', name)].decode()
        if not label:
            continue
        sections[label] = {'type': kind, 'flags': flags, 'size': size, 'alignment': alignment}
        if kind != 8:  # SHT_NOBITS has no file payload.
            payload = data[start:start + size]
            if len(payload) != size:
                raise ValueError('truncated ELF section: ' + label)
            sections[label]['sha256'] = hashlib.sha256(payload).hexdigest()
    if '.text' not in sections:
        raise ValueError('ELF code section missing')
    return sections


def inspect(path, prefix):
    data = path.read_bytes()
    record = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    if path.name.endswith('.dll'):
        text = subprocess.check_output(['objdump', '-p', str(path)], text=True)
        prefix.with_suffix('.pe.txt').write_text(text)
        record['exports'] = pe_exports(text, allow_empty=path.parent.name == 'x86_64-windows')
    else:
        symbols = subprocess.check_output(['readelf', '--wide', '--dyn-syms', str(path)], text=True)
        prefix.with_suffix('.symbols.txt').write_text(symbols)
        record['exports'] = elf_exports(symbols)
        if path.name == 'nvcuda.dll.so':
            record['winelib_exports'] = winelib_exports(data, symbols)
        record['sections'] = elf_sections(data)
        dynamic = subprocess.check_output(['readelf', '--wide', '--dynamic', str(path)], text=True)
        prefix.with_suffix('.dynamic.txt').write_text(dynamic)
        record['needed'] = re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic)
        for kind, command in [('sections', ['readelf', '--wide', '--sections']),
                              ('disassembly', ['objdump', '--disassemble'])]:
            prefix.with_suffix('.' + kind + '.txt').write_text(
                subprocess.check_output([*command, str(path)], text=True))
    return record


def compare(original, relocated, source, generated_exports, report):
    report.mkdir(parents=True, exist_ok=True)
    expected = {
        MODULES[2]: sorted(re.findall(r'^@ (?:stdcall|stub) (\w+)',
            (source / 'dlls/nvcuda/nvcuda.spec').read_text(), re.M)),
        MODULES[1]: sorted(re.findall(r'^\s+(\w+)@\d+\s*$', generated_exports.read_text(), re.M))}
    result = {'format': 'zluda-bridge-equivalence-1', 'gate': 'running',
              'source_date_epoch': os.environ.get('SOURCE_DATE_EPOCH'),
              'source_spec_sha256': hashlib.sha256((source / 'dlls/nvcuda/nvcuda.spec').read_bytes()).hexdigest(),
              'generated_pe32_exports_sha256': hashlib.sha256(generated_exports.read_bytes()).hexdigest(),
              'expected_cuda_export_counts': {name: len(names) for name, names in expected.items()},
              'behavior': 'PENDING: target-runtime fixtures and five-workload W2',
              'modules': {}, 'review_required': []}
    target = report / 'REPORT.json'
    try:
        baseline_files = sorted(path.relative_to(original).as_posix() for path in original.rglob('*')
                                if path.is_file())
        relocated_files = sorted(path.relative_to(relocated).as_posix()
                                 for path in relocated.glob('lib/wine/**/nvcuda*') if path.is_file())
        result['file_sets'] = {'original': baseline_files, 'relocated': relocated_files}
        if baseline_files != sorted(MODULES) or relocated_files != baseline_files:
            raise ValueError('bridge file set differs: ' + repr(result['file_sets']))
        for index, name in enumerate(MODULES):
            first = inspect(original / name, report / f'{index}-original')
            second = inspect(relocated / name, report / f'{index}-relocated')
            result['modules'][name] = {'original': first, 'relocated': second}
            if name in expected:
                key = 'winelib_exports' if name == MODULES[2] else 'exports'
                if sorted(item[1] for item in first[key]) != expected[name]:
                    raise ValueError('baseline CUDA exports do not cover the full spec: ' + name)
                if first[key] != second[key]:
                    raise ValueError('CUDA export name/ordinal mismatch: ' + name)
            if name.endswith('.dll'):
                if first['exports'] != second['exports']:
                    raise ValueError('PE export name/ordinal mismatch: ' + name)
            else:
                # Code sizes/hashes are separately retained for review. Names,
                # ELF type/binding/visibility and dependency order are hard gates.
                surface = lambda item: (item[0], *item[2:])
                if sorted(map(surface, first['exports'])) != sorted(map(surface, second['exports'])):
                    raise ValueError('ELF export mismatch: ' + name)
                if first['needed'] != second['needed']:
                    raise ValueError('ELF dependency mismatch: ' + name)
            if first['sha256'] != second['sha256']:
                result['review_required'].append(name)
        result['gate'] = 'file-set-exports-dependencies-pass'
        result['byte_comparison'] = 'review-required' if result['review_required'] else 'identical'
    except Exception as error:
        result['gate'] = 'failed'
        result['error'] = str(error)
        raise
    finally:
        target.write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['original', 'relocated', 'source', 'generated_exports', 'report']:
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    record = compare(args.original, args.relocated, args.source, args.generated_exports, args.report)
    print(record['gate'] + '; bytes=' + record['byte_comparison'] + '; runtime gates pending')
