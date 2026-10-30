#!/usr/bin/env python3
"""Retain zoc and the SDK loader's dependency closure; never run the compiler."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from inputs import LOCK, git, manifest, sha256
from overlay import collect_licenses


def output(*argv):
    return subprocess.check_output(list(argv), text=True, stderr=subprocess.STDOUT)


def inspect(path):
    data = path.read_bytes()[:20]
    if data[:6] != b'\x7fELF\x02\x01' or int.from_bytes(data[18:20], 'little') != 62:
        raise ValueError('zoc capsule requires ELF64 x86_64 files: ' + str(path))
    dynamic = output('readelf', '--wide', '--dynamic', str(path))
    if re.search(r'\((?:RPATH|RUNPATH)\)', dynamic):
        raise ValueError('zoc capsule cannot preserve an external ELF search path: ' + str(path))
    return re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic)


def loader_paths(text):
    paths = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r'linux-vdso\.so\.\d+\s+\(0x[0-9a-f]+\)', line):
            continue
        match = re.fullmatch(r'(\S+)\s+=>\s+(/\S+)\s+\(0x[0-9a-f]+\)', line)
        if match:
            name, filename = match.groups()
        else:
            match = re.fullmatch(r'(/\S+)\s+\(0x[0-9a-f]+\)', line)
            if not match:
                raise ValueError('unresolved/unrecognized SDK loader dependency: ' + line)
            filename = match[1]
            name = Path(filename).name
        if '/' in name or name in paths:
            raise ValueError('ambiguous SDK loader dependency: ' + name)
        paths[name] = Path(filename)
    return paths


def package_owner(path):
    choices = [str(path), str(path.resolve())]
    for name in list(choices):
        if name.startswith('/usr/lib/'):
            choices.append(name.removeprefix('/usr'))
    for name in choices:
        result = subprocess.run(['dpkg-query', '-S', name], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode == 0:
            owners = {line.rsplit(': ', 1)[0] for line in result.stdout.splitlines()}
            if len(owners) == 1:
                return owners.pop()
    raise ValueError('SDK dependency package ownership missing: ' + str(path))


def create(source, binary, dest, stack_build=None):
    if dest.exists():
        raise ValueError('zoc capsule destination must be fresh')
    needed = inspect(binary)
    program = output('readelf', '--wide', '--program-headers', str(binary))
    interpreters = re.findall(r'Requesting program interpreter:\s*([^\]]+)\]', program)
    if len(interpreters) != 1 or not Path(interpreters[0]).is_absolute():
        raise ValueError('zoc needs one absolute ELF interpreter')
    trace = output('ldd', str(binary))
    paths = loader_paths(trace)
    preload = stack_build / 'libzluda_stack_probe.so' if stack_build is not None else None
    preload_needed = inspect(preload) if preload is not None else []
    if preload is not None:
        for name, path in loader_paths(output('ldd', str(preload))).items():
            if name in paths and sha256(paths[name]) != sha256(path):
                raise ValueError('stack probe and zoc resolve different SDK runtime libraries: ' + name)
            paths[name] = path
    interpreter = Path(interpreters[0])
    if interpreter.name not in paths or paths[interpreter.name].resolve() != interpreter.resolve():
        raise ValueError('zoc interpreter differs from SDK loader resolution')
    # ldd reports the recursive closure. Independently check every ELF's
    # DT_NEEDED names are represented, before copying or retaining anything.
    inspections = {name: inspect(path) for name, path in paths.items()}
    for names in [needed, preload_needed, *inspections.values()]:
        if set(names) - paths.keys():
            raise ValueError('zoc transitive dependency closure is incomplete: ' + repr(names))
    (dest / 'bin').mkdir(parents=True)
    (dest / 'lib').mkdir()
    shutil.copyfile(binary, dest / 'bin/zoc')
    (dest / 'bin/zoc').chmod(0o755)
    libraries, owners = {}, set()
    for name, path in sorted(paths.items()):
        target = dest / 'lib' / name
        shutil.copyfile(path, target)
        target.chmod(0o755)
        owner = package_owner(path)
        owners.add(owner)
        libraries[name] = {'sdk_path': str(path), 'resolved_path': str(path.resolve()),
                           'sha256': sha256(target), 'needed': inspections[name], 'package': owner}
    for owner in sorted(owners):
        collect_licenses(Path('/usr/share/doc') / owner.split(':')[0],
                         dest / 'licenses' / owner.replace(':', '-'), required=True)
    for name in ['LICENSE-APACHE', 'LICENSE-MIT']:
        shutil.copyfile(source / name, dest / 'licenses' / name)
    # LLVM and Cargo dependencies are linked into zoc even when they do not
    # appear in DT_NEEDED. Retain their notices with this separate capsule.
    collect_licenses(source, dest / 'licenses/source', required=True)
    if 'CARGO_HOME' in os.environ:
        cargo = Path(os.environ['CARGO_HOME'])
        collect_licenses(cargo / 'registry/src', dest / 'licenses/cargo-registry', required=True)
        if (cargo / 'git/checkouts').exists():
            collect_licenses(cargo / 'git/checkouts', dest / 'licenses/cargo-git', required=True)
    shutil.copyfile(source / 'Cargo.lock', dest / 'licenses/Cargo.lock')
    if stack_build is not None:
        stack_source = source / 'wine/zluda_wine/tests/stack'
        for filename, root in [('SOURCE-SHA256SUMS', stack_source), ('SHA256SUMS', stack_build)]:
            for line in (stack_build / filename).read_text().splitlines():
                digest, name = line.split(None, 1)
                if not (root / name).resolve().is_relative_to(root.resolve()) or sha256(root / name) != digest:
                    raise ValueError('stack probe source/artifact checksum mismatch: ' + name)
        target = dest / 'stack'
        target.mkdir()
        for name in ['libzluda_stack_probe.so', 'TOOLCHAIN.txt', 'SOURCE-SHA256SUMS', 'SHA256SUMS']:
            shutil.copyfile(stack_build / name, target / name)
        for name in ['stack-probe.c', 'build-probe.sh', 'measure.py', 'README.md']:
            shutil.copyfile(stack_source / name, target / name)
    (dest / 'SDK-PACKAGES.txt').write_text(output('dpkg-query', '-W', *sorted(owners)))
    (dest / 'SDK-LDD.txt').write_text(trace)
    wrapper = dest / 'zoc'
    wrapper.write_text('#!/bin/sh\nset -eu\n'
        'capsule=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
        '# Preserve LD_PRELOAD for the separately approved stack measurement.\n'
        'exec "$capsule/lib/' + interpreter.name + '" --library-path "$capsule/lib" "$capsule/bin/zoc" "$@"\n')
    wrapper.chmod(0o755)
    record = {'kind': 'offline compiler measurement capsule; no PTX inputs or execution',
              'source_commit': git(source, 'rev-parse', 'HEAD'), 'sdk_image': LOCK['sdk_image'],
              'llvm': LOCK['llvm'], 'rust_version': LOCK['rust_version'],
              'cargo_lock_sha256': sha256(source / 'Cargo.lock'),
              'binary_sha256': sha256(binary), 'interpreter': interpreter.name,
              'needed': needed, 'libraries': libraries,
              'stack_probe_sha256': sha256(preload) if preload is not None else None,
              'execution': 'NOT RUN; measurement needs separate coordinator clearance and explicit --arch gfx1030'}
    (dest / 'PROVENANCE.json').write_text(json.dumps(record, indent=2) + '\n')
    manifest(dest)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'binary', 'dest']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stack-probe-build', type=Path, required=True)
    args = parser.parse_args()
    create(args.source, args.binary, args.dest, args.stack_probe_build)
