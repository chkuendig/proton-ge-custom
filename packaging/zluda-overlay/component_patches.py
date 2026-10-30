#!/usr/bin/env python3
"""Admit upstream pins and hash-locked GE patch series in private build trees."""
import argparse
import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from inputs import LOCK, check_tree, git, sha256


def series(root, name, lock=None):
    lock = LOCK if lock is None else lock
    spec = lock['component_patches'][name]
    entries = spec['patches']
    expected = [entry['path'] for entry in entries]
    actual = sorted(path.relative_to(root).as_posix() for path in (root / 'patches' / name).glob('*.patch'))
    if expected != actual or len(set(expected)) != len(expected):
        raise ValueError('component patch inventory differs: ' + name)
    for entry in entries:
        path = root / entry['path']
        if path.is_symlink() or not path.is_file() or sha256(path) != entry['sha256']:
            raise ValueError('component patch checksum differs: ' + entry['path'])
    return entries


def check_inputs(root, name, lock=None):
    lock = LOCK if lock is None else lock
    entries = series(root, name, lock)
    base = lock['components'][name]
    child = root / name
    if git(root, 'rev-parse', 'HEAD:' + name) != base or git(child, 'rev-parse', 'HEAD') != base:
        raise ValueError('component upstream gitlink differs: ' + name)
    if git(child, 'rev-parse', 'HEAD^{tree}') != lock['component_patches'][name]['upstream_tree']:
        raise ValueError('component upstream tree differs: ' + name)
    if git(child, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('dirty upstream component: ' + name)
    check_tree(child)
    return entries


def gitlinks(repo):
    for line in git(repo, 'ls-tree', '-r', 'HEAD').splitlines():
        meta, path = line.split('\t', 1)
        mode, _, commit = meta.split()
        if mode == '160000':
            yield path, commit


def copy_repository(source, destination):
    # Independent indexes/refs, shared immutable objects, no remote fetch or
    # checkout filters. Copy admitted tracked bytes (including smudged LFS),
    # never ignored build files, and prepare each gitlink the same way.
    destination.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='.component-git-', dir=destination.parent) as temporary:
        clone = Path(temporary) / 'metadata'
        subprocess.run(['git', 'clone', '--quiet', '--shared', '--no-checkout', '--',
                        str(source), str(clone)], check=True)
        shutil.move(str(clone / '.git'), destination / '.git')
    git(destination, 'read-tree', 'HEAD')
    for line in git(source, 'ls-tree', '-r', 'HEAD').splitlines():
        meta, path = line.split('\t', 1)
        mode, _, _ = meta.split()
        child, target = source / path, destination / path
        if mode == '160000':
            copy_repository(child, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            if child.is_symlink():
                target.symlink_to(child.readlink())
            else:
                shutil.copyfile(child, target)
                target.chmod(0o755 if mode == '100755' else 0o644)
    git(destination, 'update-index', '--refresh')


def apply_series(repo, root, entries, cached=False):
    for entry in entries:
        subprocess.run(['git', '-C', str(repo), 'apply', '--cached' if cached else '--index',
                        '--whitespace=nowarn', str(root / entry['path'])], check=True)


def prepare(root, destination, name, lock=None):
    lock = LOCK if lock is None else lock
    entries = check_inputs(root, name, lock)
    if destination.exists():
        raise ValueError('prepared component directory must be new: ' + str(destination))
    copy_repository(root / name, destination)
    apply_series(destination, root, entries)
    tree = git(destination, 'write-tree')
    if tree != lock['component_patches'][name]['prepared_tree']:
        raise ValueError('prepared component tree differs: ' + name)
    check_tree(destination)
    return receipt(root, destination, name, lock)


def receipt(root, prepared, name, lock):
    spec = lock['component_patches'][name]
    if git(prepared, 'rev-parse', 'HEAD') != lock['components'][name] or git(prepared, 'write-tree') != spec['prepared_tree']:
        raise ValueError('prepared component index differs: ' + name)
    git(prepared, 'diff', '--quiet', '--ignore-submodules=all')
    check_tree(prepared)
    return {'component': name, 'upstream_commit': lock['components'][name],
            'upstream_tree': spec['upstream_tree'], 'prepared_tree': spec['prepared_tree'],
            'vcs_description': git(prepared, 'describe', '--always', '--tags', '--dirty=+'),
            'fork_reference_commit': spec['fork_commit'], 'fork_reference_tree': spec['fork_tree'],
            'patches': series(root, name, lock)}


def source_trees(repo, prefix=''):
    yield prefix, repo, git(repo, 'write-tree')
    for path, _ in gitlinks(repo):
        yield from source_trees(repo / path, prefix + path + '/')


def stage_sources(root, prepared, name, dest, lock=None):
    lock = LOCK if lock is None else lock
    record = receipt(root, prepared, name, lock)
    epoch = int(git(root, 'show', '-s', '--format=%ct', 'HEAD'))
    sources = dest / 'sources'
    sources.mkdir(parents=True, exist_ok=True)
    record['submodules'] = {}
    with (sources / (name + '-prepared.tar.gz')).open('wb') as output:
        with gzip.GzipFile(filename='', fileobj=output, mode='wb', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|') as archive:
                for prefix, repo, tree in source_trees(prepared):
                    if prefix:
                        record['submodules'][prefix.rstrip('/')] = git(repo, 'rev-parse', 'HEAD')
                    payload = subprocess.check_output(['git', '-C', str(repo), 'archive', tree])
                    with tarfile.open(fileobj=io.BytesIO(payload)) as upstream:
                        for member in upstream:
                            member.name = prefix + member.name
                            member.mtime, member.uid, member.gid = epoch, 0, 0
                            member.uname = member.gname = ''
                            for key in ['path', 'mtime', 'atime', 'ctime']:
                                member.pax_headers.pop(key, None)
                            archive.addfile(member, upstream.extractfile(member) if member.isfile() else None)
    for entry in record['patches']:
        target = sources / 'component-patches' / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / entry['path'], target)
    provenance = dest / 'provenance'
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / (name + '-PATCHES.json')).write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['prepare', 'stage'])
    parser.add_argument('source', type=Path)
    parser.add_argument('prepared', type=Path)
    parser.add_argument('component', choices=['dxvk-nvapi', 'protonfixes'])
    parser.add_argument('--dest', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(args.source, args.prepared, args.component)
    else:
        if args.dest is None:
            parser.error('stage requires --dest')
        result = stage_sources(args.source, args.prepared, args.component, args.dest)
    print(json.dumps(result, indent=2))
