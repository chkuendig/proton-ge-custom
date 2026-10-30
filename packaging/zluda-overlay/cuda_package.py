#!/usr/bin/env python3
"""Install and audit the immutable CUDA namespaces; never load a binary."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import struct
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import proton_zluda as policy

MODULES = ['x86_64-windows/nvcuda.dll', 'i386-windows/nvcuda.dll', 'x86_64-unix/nvcuda.so']
NAMES = {'nvcuda.dll', 'nvcuda.dll.so', 'nvcuda.so'}
# Windows system runtimes supplied by Wine, never MinGW redistributable DLLs.
WINDOWS_IMPORTS = {'kernel32.dll', 'msvcrt.dll', 'ntdll.dll', 'userenv.dll',
                   'ws2_32.dll', 'bcryptprimitives.dll', 'dbghelp.dll',
                   'advapi32.dll', 'ucrtbase.dll'}



def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pe_info(path, require_builtin=True):
    data = path.read_bytes()
    def unpack(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError('PE field outside file: ' + str(path))
        return struct.unpack_from(fmt, data, offset)
    if data[:2] != b'MZ' or (require_builtin and b'Wine builtin DLL' not in data[0x40:0x60]):
        raise ValueError('not a builtin-marked PE: ' + str(path))
    nt, = unpack('<I', 0x3c)
    if data[nt:nt + 4] != b'PE\0\0':
        raise ValueError('missing PE header: ' + str(path))
    machine, sections, _, _, _, optional_size, _ = unpack('<HHIIIHH', nt + 4)
    optional = nt + 24
    magic, = unpack('<H', optional)
    if magic not in (0x10b, 0x20b):
        raise ValueError('invalid PE optional header')
    directory = optional + {0x10b: 96, 0x20b: 112}[magic]
    directories, = unpack('<I', directory - 4)
    if not 2 <= directories <= 16 or directory + directories * 8 > optional + optional_size:
        raise ValueError('invalid PE directory extent')
    if optional + optional_size > len(data):
        raise ValueError('PE optional header outside file')
    if directories > 13 and any(unpack('<II', directory + 13 * 8)):
        raise ValueError('unexpected PE delayed import directory')
    export_rva, export_size = unpack('<II', directory)
    def offset(rva, size=1):
        for i in range(sections):
            _, virtual_size, virtual, length, position = unpack('<8sIIII', optional + optional_size + i * 40)
            if virtual <= rva < virtual + max(virtual_size, length):
                if rva - virtual + size > length or position + rva - virtual + size > len(data):
                    raise ValueError('PE RVA outside file-backed section: ' + str(path))
                return position + rva - virtual
        raise ValueError('unmapped PE export RVA: ' + str(path))
    count = 0 if not export_rva or not export_size else unpack('<I', offset(export_rva) + 24)[0]
    imports = []
    rva, size = unpack('<II', directory + 8)
    if rva or size:
        stride = 20
        if not rva or size < stride or size // stride > 65536:
            raise ValueError('invalid PE import directory')
        terminated = False
        for position in range(0, size - stride + 1, stride):
            record = unpack('<IIIII', offset(rva + position, stride))
            if not any(record):
                terminated = True
                break
            name_rva = record[3]
            if not name_rva:
                raise ValueError('PE import DLL name is missing')
            start = offset(name_rva)
            end = data.find(b'\0', start, min(start + 1024, len(data)))
            if end < 0 or end == start:
                raise ValueError('unterminated/empty PE import DLL name')
            offset(name_rva, end - start + 1)
            try:
                imports.append(data[start:end].decode('ascii'))
            except UnicodeDecodeError as error:
                raise ValueError('non-ASCII PE import DLL name') from error
        if not terminated:
            raise ValueError('unterminated PE import descriptor table')
    return {'machine': machine, 'optional_magic': magic, 'named_exports': count,
            'imports': imports, 'delay_imports': []}


def check_pe_imports(info):
    names = info['imports'] + info['delay_imports']
    forbidden = sorted({name for name in names if name.lower() not in WINDOWS_IMPORTS
                        and not re.fullmatch(r'(?:api|ext)-ms-win-[a-z0-9-]+\.dll', name.lower())})
    if forbidden:
        raise ValueError('unexpected PE bridge DLL dependency: ' + ', '.join(forbidden))


def elf_info(path, require_wow64=True):
    data = path.read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 62:
        raise ValueError('Unix CUDA bridge must be ELF64 x86_64')
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
    symbols, needed = set(), []
    for section in sections:
        if section[1] == 11:  # SHT_DYNSYM
            table = content(sections[section[6]])
            for record in struct.iter_unpack('<IBBHQQ', content(section)):
                name, info, visibility, index, _, _ = record
                if index and info >> 4 in (1, 2) and visibility & 3 == 0:
                    symbols.add(string(table, name))
        if section[1] == 6:  # SHT_DYNAMIC
            table = content(sections[section[6]])
            needed += [string(table, value) for tag, value in struct.iter_unpack('<qQ', content(section)) if tag == 1]
    tables = {'__wine_unix_call_funcs', '__wine_unix_call_wow64_funcs'}
    required = tables if require_wow64 else {'__wine_unix_call_funcs'}
    if not required <= symbols:
        raise ValueError('Unix CUDA bridge missing native/WoW64 call tables')
    allowed = {'libc.so.6', 'libgcc_s.so.1', 'libdl.so.2', 'libpthread.so.0',
               'librt.so.1', 'libm.so.6', 'ld-linux-x86-64.so.2'}
    if set(needed) - allowed:
        raise ValueError('unexpected bridge dependency: ' + ', '.join(sorted(set(needed) - allowed)))
    return {'needed': needed, 'call_tables': sorted(tables & symbols)}


def inspect_draft(artifacts, commit):
    """Inspect x64 compile outputs without installing or certifying a package."""
    names = {'nvcuda.dll', 'nvcuda.so'}
    checksums = {}
    for line in (artifacts / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        if name in checksums or name not in names or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('invalid x64 draft checksum entry')
        checksums[name] = expected
    if set(checksums) != names:
        raise ValueError('incomplete x64 draft output inventory')
    for name, expected in checksums.items():
        path = artifacts / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError('x64 draft checksum/regular-file failure: ' + name)
    pe = pe_info(artifacts / 'nvcuda.dll')
    check_pe_imports(pe)
    if pe['machine'] != 0x8664:
        raise ValueError('x64 draft PE machine mismatch')
    native = elf_info(artifacts / 'nvcuda.so', require_wow64=False)
    record = {'format': 'zluda-wine-x64-compile-draft-1', 'source_commit': commit,
              'sha256': checksums, 'pe': pe, 'unix': native,
              'x64_export_count': 'matches' if pe['named_exports'] == 788 else 'review-required',
              'full_packaging_gate': 'NOT RUN: PE32/three-module/WoW64 contract is separate',
              'qualification': 'UNQUALIFIED: compile evidence only; no packaging or ABI/runtime parity'}
    (artifacts / 'X64-COMPILE-DRAFT.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def install_bridge(artifacts, tool, commit):
    contract = json.loads((artifacts / 'BUILD-CONTRACT.json').read_text())
    if contract.get('contract') != 'zluda-wine-packaging-1' or contract.get('source_commit') != commit:
        raise ValueError('bridge contract/source pin mismatch')
    if contract.get('modules') != MODULES or not contract.get('rust', '').startswith('rustc 1.99.0 '):
        raise ValueError('bridge target/toolchain contract mismatch')
    checksums = {}
    for line in (artifacts / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        if not re.fullmatch('[0-9a-f]{64}', expected) or name in checksums or name not in MODULES:
            raise ValueError('unexpected/duplicate bridge checksum entry: ' + name)
        checksums[name] = expected
    if set(checksums) != set(MODULES):
        raise ValueError('incomplete bridge checksum inventory')
    target = tool / 'files/lib/wine-cuda/zluda'
    inspections = {}
    for name in MODULES:
        source = artifacts / name
        if source.is_symlink() or digest(source) != checksums[name]:
            raise ValueError('bridge checksum/regular-file failure: ' + name)
        if name.endswith('.dll'):
            info = pe_info(source)
            check_pe_imports(info)
            expected = (0x8664, 788) if name.startswith('x86_64') else (0x14c, 690)
            if (info['machine'], info['named_exports']) != expected:
                raise ValueError('bridge PE architecture/export surface mismatch: ' + name)
        else:
            info = elf_info(source)
        inspections[name] = info
        out = target / name
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.is_symlink():
            out.unlink()
        shutil.copyfile(source, out)
        out.chmod(0o755 if name.endswith('.so') else 0o644)
    provenance = tool / 'provenance'
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / 'ZLUDA-WINE.json').write_text(json.dumps(
        {'contract': contract, 'sha256': checksums, 'inspection': inspections,
         'qualification': 'PENDING: equivalence, loader/prefix and target-runtime W1/W2 gates'}, indent=2) + '\n')
    return inspections


def write_stubs(tool, originals):
    cuda = tool / 'files/lib/wine-cuda'
    for arch, data in originals.items():
        if hashlib.sha256(data).hexdigest() != policy.STUB_SHA256[arch]:
            raise ValueError('GE stub does not match pinned hash: ' + arch)
        out = cuda / 'stub' / arch / 'nvcuda.dll'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        out.write_bytes(data)
        out.chmod(0o644)
        info = pe_info(out)
        if (info['machine'], info['named_exports']) != (0x8664 if arch.startswith('x86_64') else 0x14c, 0):
            raise ValueError('GE stub is not an empty builtin PE: ' + arch)
    (cuda / 'stubs.json').write_text(json.dumps(
        {'ge_archive_sha256': policy.GE_ARCHIVE_SHA256, 'sha256': policy.STUB_SHA256,
         'migration_policy': 'Wine builtin/placeholder PE, ELF and managed module symlinks'}, indent=2) + '\n')


def prepare_stubs(tool):
    """Call while tool still contains the untouched checksum-locked GE tree."""
    originals = {}
    for arch in policy.STUB_SHA256:
        original = tool / 'files/lib/wine' / arch / 'nvcuda.dll'
        if original.is_symlink():
            raise ValueError('GE stub must be a regular file: ' + str(original))
        originals[arch] = original.read_bytes()
    write_stubs(tool, originals)
    materialize_template(tool)


def archive_stubs(archive, tool):
    # Stream the checksum so the half-gigabyte archive never resides in RAM.
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != policy.GE_ARCHIVE_SHA256:
            raise ValueError('GE archive checksum mismatch')
    originals = {}
    with tarfile.open(archive) as stream:
        for member in stream:
            for arch in policy.STUB_SHA256:
                if member.name.split('/', 1)[-1] == 'files/lib/wine/' + arch + '/nvcuda.dll':
                    if not member.isfile() or arch in originals or member.size > 1024 * 1024:
                        raise ValueError('invalid/duplicate GE stub archive member')
                    originals[arch] = stream.extractfile(member).read()
    if set(originals) != set(policy.STUB_SHA256):
        raise ValueError('GE archive lacks both CUDA stub modules')
    write_stubs(tool, originals)


def materialize_template(tool):
    policy.materialize_default_prefix(tool, tool / 'files/share/default_pfx')


def remove_canonical(tool):
    canonical = tool / 'files/lib/wine'
    for path in canonical.rglob('*'):
        if path.name.lower() in NAMES:
            if path.is_dir() and not path.is_symlink():
                raise ValueError('unexpected canonical CUDA directory: ' + str(path))
            path.unlink()
    legacy = canonical / 'nvidia-libs/nvcuda'
    if legacy.is_symlink():
        legacy.unlink()
    elif legacy.exists():
        shutil.rmtree(legacy)


def audit(tool):
    cuda, record = policy.stub_assets(tool)
    expected_stub = {cuda / 'stub' / arch / suffix for arch in policy.ARCHES.values() for suffix in ['', 'nvcuda.dll']}
    if (cuda / 'stub').is_symlink() or set((cuda / 'stub').rglob('*')) != expected_stub:
        raise ValueError('off CUDA root contains extra files or a Unix/backend alias')
    for canonical in [tool / 'files/lib/wine', tool / 'files/lib64/wine']:
        if not canonical.resolve().is_relative_to(tool.resolve()):
            raise ValueError('canonical Wine alias leaves tool')
        if any(p.name.lower() in NAMES for p in canonical.rglob('*')):
            raise ValueError('canonical nvcuda remains: ' + str(canonical))
    expected_on = {cuda / 'zluda' / name for name in MODULES}
    expected_on |= {p.parent for p in expected_on}
    if (cuda / 'zluda').is_symlink() or set((cuda / 'zluda').rglob('*')) != expected_on:
        raise ValueError('on CUDA root file set differs from three-module contract')
    for name in MODULES:
        path = cuda / 'zluda' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('on CUDA module must be a regular file: ' + name)
        if name.endswith('.dll'):
            info = pe_info(path)
            check_pe_imports(info)
            expected = (0x8664, 788) if name.startswith('x86_64') else (0x14c, 690)
            if (info['machine'], info['named_exports']) != expected:
                raise ValueError('on CUDA PE architecture/export mismatch: ' + name)
        else:
            elf_info(path)
    policy.validate_default_prefix(tool, tool / 'files/share/default_pfx')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['install', 'stubs', 'template', 'clean', 'audit', 'archive-stubs', 'licenses', 'draft'])
    parser.add_argument('tool', type=Path)
    parser.add_argument('--artifacts', type=Path)
    parser.add_argument('--commit')
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    if args.command == 'draft':
        inspect_draft(args.tool, args.commit)
    elif args.command == 'archive-stubs':
        archive_stubs(args.archive, args.tool)
    elif args.command == 'licenses':
        from overlay import collect_licenses
        collect_licenses(args.source / 'wine/zluda_wine', args.tool / 'licenses/zluda-wine')
        for name in ['LICENSE-MIT', 'LICENSE-APACHE']:
            out = args.tool / 'licenses/zluda-wine' / name
            shutil.copyfile(args.source / name, out)
        shutil.copyfile(args.source / 'wine/zluda_wine/Cargo.lock', args.tool / 'licenses/zluda-wine/Cargo.lock')
        cargo = Path(os.environ['CARGO_HOME'])
        collect_licenses(cargo / 'registry/src', args.tool / 'licenses/cargo/registry')
        if (cargo / 'git/checkouts').exists():
            collect_licenses(cargo / 'git/checkouts', args.tool / 'licenses/cargo/git')
    elif args.command == 'install':
        install_bridge(args.artifacts, args.tool, args.commit)
    else:
        {'stubs': prepare_stubs, 'template': materialize_template, 'clean': remove_canonical, 'audit': audit}[args.command](args.tool)
