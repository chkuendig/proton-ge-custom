#!/usr/bin/env python3
"""Archive the pinned superproject and compiler-observed submodule inputs."""
import argparse
import gzip
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile
import tempfile

from inputs import HERE, git, sha256


def observed_inputs(source, build, required=True):
    paths = set()
    records = []
    for path in sorted(build.rglob('*')):
        if not path.is_file() or (path.suffix != '.d' and path.name != 'output'):
            continue
        text = path.read_text(errors='replace')
        if path.suffix == '.d':
            lexer = shlex.shlex('\n'.join(line for line in text.replace('\\\n', ' ').splitlines()
                                                   if not line.startswith('#')), posix=True)
            lexer.whitespace_split, lexer.commenters, lexer.quotes = True, '', ''
            tokens = list(lexer)
            candidates = [token.rstrip(':') for token in tokens]
        else:
            candidates = re.findall(r'^cargo:{1,2}rerun-if-changed=(.+)$', text, re.M)
        accepted = []
        for value in candidates:
            candidate = Path(value)
            if candidate.is_absolute():
                candidate = candidate.resolve()
            if candidate.is_absolute() and candidate.is_relative_to(source):
                if not candidate.exists():
                    raise ValueError('missing compiler-observed source: ' + value)
                if candidate.is_file():
                    paths.add(candidate.relative_to(source).as_posix())
                    accepted.append(candidate.relative_to(source).as_posix())
        if accepted:
            records.append({'dep_info': str(path.relative_to(build)), 'sha256': sha256(path),
                            'source_inputs': sorted(set(accepted))})
    if not records and required:
        raise ValueError('no Cargo dep-info/build-script source inputs observed')
    return paths, records


def check_extracted(root, metadata, compiled=None):
    """Resolve literal Rust includes in Cargo's actual local dependency closure."""
    checked = []
    for package in metadata['packages']:
        if package.get('source') is not None:
            continue
        directory = Path(package['manifest_path']).parent
        if not directory.is_relative_to(root):
            raise ValueError('Cargo local dependency escaped extracted archive')
        files = directory.rglob('*.rs') if compiled is None else (
            root / name for name in compiled if name.endswith('.rs') and
            (root / name).is_relative_to(directory))
        for file in files:
            # Generated target files are not source regeneration inputs.
            if 'target' in file.relative_to(directory).parts:
                continue
            text = file.read_text()
            for match in re.finditer(r'\binclude(?:_str|_bytes)?!\s*[({]\s*"([^"\n]+)"', text):
                candidate = (file.parent / match[1]).resolve()
                if not candidate.is_relative_to(root) or not candidate.is_file():
                    raise ValueError('unresolved extracted include: ' + str(file) + ': ' + match[1])
                checked.append(candidate.relative_to(root).as_posix())
    return sorted(set(checked))


def sdk_inputs(env):
    sdk, receipt = Path(env['WINE_SDK_ROOT']), Path(env['ZLUDA_WINE_ABI_RECEIPT'])
    if sha256(receipt) != env['ZLUDA_WINE_ABI_RECEIPT_SHA256']:
        raise ValueError('source archive ABI receipt digest mismatch')
    record = json.loads(receipt.read_text())
    files = {}
    for name, expected in record['sdk_files'].items():
        path = Path(name)
        if (not path.parts or path.is_absolute() or '..' in path.parts or
                path.parts[0] in {'include', 'include-generated'} or
                path.suffix in {'.h', '.c', '.idl', '.rs'} or sha256(sdk / path) != expected):
            raise ValueError('source archive SDK input mismatch: ' + name)
        files['external/prepared-wine-sdk/' + name] = (sdk / path).read_bytes()
    if not {'bin/winebuild', 'lib/ntdll.so'} <= record['sdk_files'].keys():
        raise ValueError('source archive lacks SDK binary/provider')
    return files


def extracted_environment(root, original):
    # Relocate both the digest-pinned attestation and its exact binary/tool
    # inputs. Neither receipt nor wrapper points into the original build tree.
    record = json.loads((root / 'external/BRIDGE-BUILD.json').read_text())
    env = dict(original)
    for key in ['WINE_SOURCE_ROOT', 'ZLUDA_WINE_WINE_INCLUDE', 'ZLUDA_WINE_WINE_GENERATED']:
        env.pop(key, None)
    env.update(WINE_SDK_ROOT=str(root / 'external/prepared-wine-sdk'),
               ZLUDA_WINE_ABI_RECEIPT=str(root / 'external/prepared-wine-abi.json'),
               ZLUDA_WINE_ABI_RECEIPT_SHA256=record['abi_receipt_sha256'],
               ZLUDA_WINE_SOURCE_ID=record['source_commit'], SOURCE_DATE_EPOCH=record['source_date_epoch'])
    sdk_inputs(env)  # Actual bytes, before Cargo also performs production admission.
    return env


def create(source, build, output, cargo='cargo'):
    source = source.resolve()
    paths, records = observed_inputs(source, build)
    entries = {}
    for line in git(source, 'ls-tree', '-r', 'HEAD').splitlines():
        meta, name = line.split('\t', 1)
        mode, kind, oid = meta.split()
        if kind == 'blob':
            entries[name] = (mode, oid)
    extras = sorted(paths - entries.keys())
    # Submodules remain at their admitted pins. Only actual source inputs read
    # by Cargo are copied, never LLVM/other complete submodule repositories.
    for name in extras:
        path = source / name
        repo = Path(git(path.parent, 'rev-parse', '--show-toplevel'))
        if repo == source or not repo.is_relative_to(source):
            raise ValueError('untracked build source: ' + name)
        relative = path.relative_to(repo).as_posix()
        oid = git(repo, 'rev-parse', 'HEAD:' + relative)
        if git(repo, 'hash-object', '--', relative) != oid:
            raise ValueError('modified submodule build source: ' + name)
    capsule = sdk_inputs(os.environ)
    capsule['external/prepared-wine-abi.json'] = Path(os.environ['ZLUDA_WINE_ABI_RECEIPT']).read_bytes()
    identity = {'source_commit': git(source, 'rev-parse', 'HEAD'),
                'source_date_epoch': os.environ['SOURCE_DATE_EPOCH'],
                'abi_receipt_sha256': os.environ['ZLUDA_WINE_ABI_RECEIPT_SHA256']}
    if os.environ['ZLUDA_WINE_SOURCE_ID'] != identity['source_commit']:
        raise ValueError('source archive shipping source identity mismatch')
    capsule['external/BRIDGE-BUILD.json'] = (json.dumps(identity, indent=2) + '\n').encode()
    for path in HERE.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            capsule['external/proton-build-recipes/' + path.relative_to(HERE).as_posix()] = path.read_bytes()
    if set(capsule) & entries.keys():
        raise ValueError('prepared SDK capsule collides with tracked source')
    raw = subprocess.check_output(['git', '-C', str(source), 'archive', 'HEAD'])
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'source_commit': git(source, 'rev-parse', 'HEAD'), 'source_tree': git(source, 'rev-parse', 'HEAD^{tree}'),
               'method': 'full tracked superproject plus Cargo-observed submodule files',
               'cargo_inputs': records, 'extra_submodule_files': {name: sha256(source / name) for name in extras},
               'production_license': 'MIT OR Apache-2.0',
               'reference_license': 'Source archive also contains retained LGPL C/test material; not relicensed',
               'compiled_source_closure': sorted(paths),
               'prepared_abi_receipt_sha256': identity['abi_receipt_sha256'],
               'sdk_capsule': {name: __import__('hashlib').sha256(data).hexdigest() for name,data in capsule.items()}}
    with output.open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w|') as archive, tarfile.open(fileobj=io.BytesIO(raw)) as original:
            for member in original:
                archive.addfile(member, original.extractfile(member) if member.isfile() else None)
            for name in extras:
                info = tarfile.TarInfo(name)
                data = (source / name).read_bytes()
                info.size, info.mode = len(data), (source / name).stat().st_mode & 0o777
                archive.addfile(info, io.BytesIO(data))
            for name, data in sorted(capsule.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), (0o755 if name.endswith('/bin/winebuild') else 0o644)
                archive.addfile(info, io.BytesIO(data))
    with tempfile.TemporaryDirectory(prefix='archive-check-', dir=build) as temporary:
        root = Path(temporary).resolve()
        with tarfile.open(output) as archive:
            for member in archive:
                target = (root / member.name).resolve()
                if not target.is_relative_to(root) or not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError('unsafe source archive member: ' + member.name)
                if member.issym() and not (target.parent / member.linkname).resolve().is_relative_to(root):
                    raise ValueError('source archive link escapes extraction')
            archive.extractall(root)
        # Only offline registry sources already fetched for the real build.
        result = subprocess.check_output([cargo, 'metadata', '--locked', '--offline', '--format-version=1',
                         '--manifest-path', str(root / 'wine/zluda_wine/Cargo.toml')], text=True)
        receipt['resolved_includes'] = check_extracted(root, json.loads(result), paths)
        # Run the actual generator/proc macros from the extracted tree, never
        # the original checkout. This checks dynamic build.rs reads and
        # OUT_DIR includes in addition to literal source includes.
        env = dict(extracted_environment(root, os.environ),
                   CARGO_TARGET_DIR=str(root / 'target'), CARGO_BUILD_JOBS='3')
        check = [cargo, 'check', '--locked', '--offline', '--lib', '--target', 'x86_64-unknown-linux-gnu',
                 '--manifest-path', str(root / 'wine/zluda_wine/Cargo.toml')]
        with output.with_suffix(output.suffix + '.check.log').open('w') as log:
            subprocess.run(check, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        receipt['extracted_cargo_check'] = 'passed: offline native library and build-script regeneration'

        for name in paths:
            if not (root / name).is_file() or sha256(root / name) != sha256(source / name):
                raise ValueError('observed build input missing/different in extracted archive: ' + name)
    receipt['archive_sha256'] = sha256(output)
    output.with_suffix(output.suffix + '.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def rebuild(archive_path, work, cargo='cargo'):
    """Build all three bridge targets offline from shipped sources in a fresh tree."""
    work.mkdir(parents=True, exist_ok=False)
    root = work / 'source'
    root.mkdir()
    with tarfile.open(archive_path) as archive:
        for member in archive:
            target = (root / member.name).resolve()
            if not target.is_relative_to(root) or not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('unsafe source archive member: ' + member.name)
            if member.issym() and not (target.parent / member.linkname).resolve().is_relative_to(root):
                raise ValueError('source archive link escapes extraction')
        archive.extractall(root)
    env = dict(extracted_environment(root, os.environ),
               CARGO_TARGET_DIR=str(work / 'target'), CARGO_BUILD_JOBS='3')
    record = {'archive_sha256': sha256(archive_path), 'status': 'running', 'targets': {}}
    try:
        for target, triplet, artifact in [
            ('x86_64-unknown-linux-gnu', None, 'libnvcuda.so'),
            ('x86_64-pc-windows-gnu', 'x86_64', 'libnvcuda.a'),
            ('i686-pc-windows-gnu', 'i686', 'libnvcuda.a')]:
            local = dict(env)
            if triplet:
                local.update(ZLUDA_WINE_PE_CC=triplet + '-w64-mingw32-gcc',
                             ZLUDA_WINE_PE_AR=triplet + '-w64-mingw32-ar')
            command = [cargo, 'build', '--locked', '--offline', '--release', '--lib', '--target', target,
                       '--manifest-path', str(root / 'wine/zluda_wine/Cargo.toml')]
            with (work / (target + '.log')).open('w') as log:
                subprocess.run(command, cwd=root / 'wine/zluda_wine', env=local,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            output = work / 'target' / target / 'release' / artifact
            if not output.is_file() or not output.stat().st_size:
                raise ValueError('offline build artifact missing: ' + str(output))
            record['targets'][target] = {'status': 'passed', 'artifact_sha256': sha256(output),
                                         'command': command}
        record['status'] = 'passed'
    finally:
        (work / 'REBUILD.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--rebuild-archive', type=Path)
    parser.add_argument('--work', type=Path)
    for name in ['source', 'build', 'output']:
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args()
    if args.rebuild_archive:
        if args.work is None:
            parser.error('--work required with --rebuild-archive')
        rebuild(args.rebuild_archive, args.work)
    else:
        if any(value is None for value in (args.source, args.build, args.output)):
            parser.error('--source, --build and --output required')
        create(args.source, args.build, args.output)
