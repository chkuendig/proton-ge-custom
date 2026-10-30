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

from inputs import git, sha256


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


def check_extracted(root, metadata):
    """Resolve literal Rust includes in Cargo's actual local dependency closure."""
    checked = []
    for package in metadata['packages']:
        if package.get('source') is not None:
            continue
        directory = Path(package['manifest_path']).parent
        if not directory.is_relative_to(root):
            raise ValueError('Cargo local dependency escaped extracted archive')
        for file in directory.rglob('*.rs'):
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
    wine = Path(os.environ['WINE_SOURCE_ROOT']).resolve() if 'WINE_SOURCE_ROOT' in os.environ else None
    wine_paths, wine_records = observed_inputs(wine, build, required=False) if wine is not None else (set(), [])
    for name in wine_paths:
        if 'external/wine-source/' + name in entries:
            raise ValueError('external Wine input collides with tracked source: ' + name)
    raw = subprocess.check_output(['git', '-C', str(source), 'archive', 'HEAD'])
    output.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'source_commit': git(source, 'rev-parse', 'HEAD'), 'source_tree': git(source, 'rev-parse', 'HEAD^{tree}'),
               'method': 'full tracked superproject plus Cargo-observed submodule files',
               'cargo_inputs': records, 'extra_submodule_files': {name: sha256(source / name) for name in extras},
               'wine_source_inputs': wine_records,
               'wine_source_files': {name: sha256(wine / name) for name in sorted(wine_paths)}}
    with output.open('wb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w|') as archive, tarfile.open(fileobj=io.BytesIO(raw)) as original:
            for member in original:
                archive.addfile(member, original.extractfile(member) if member.isfile() else None)
            for name in extras:
                info = tarfile.TarInfo(name)
                data = (source / name).read_bytes()
                info.size, info.mode = len(data), (source / name).stat().st_mode & 0o777
                archive.addfile(info, io.BytesIO(data))
            for name in sorted(wine_paths):
                data = (wine / name).read_bytes()
                info = tarfile.TarInfo('external/wine-source/' + name)
                info.size, info.mode = len(data), 0o644
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
        receipt['resolved_includes'] = check_extracted(root, json.loads(result))
        # Run the actual generator/proc macros from the extracted tree, never
        # the original checkout. This checks dynamic build.rs reads and
        # OUT_DIR includes in addition to literal source includes.
        env = dict(os.environ, CARGO_TARGET_DIR=str(root / 'target'), CARGO_BUILD_JOBS='3')
        if wine is not None:
            env['WINE_SOURCE_ROOT'] = str(root / 'external/wine-source')
        if 'WINE_SDK_ROOT' in env:
            env['ZLUDA_WINE_WINE_INCLUDE'] = str(Path(env['WINE_SDK_ROOT']) / 'include')
            env['ZLUDA_WINE_WINE_GENERATED'] = str(Path(env['WINE_SDK_ROOT']) / 'include-generated')
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'build', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    create(args.source, args.build, args.output)
