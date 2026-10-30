#!/usr/bin/env python3
"""Stage bounded CPU-gate evidence without following Wine prefix symlinks."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import stat

PROFILES = ('win64', 'wow64')
PHASES = ('off-before', 'on', 'off-after')
EXCLUDED = {'dosdevices', 'drive_c', 'pfx', 'prefix', 'compatdata', 'tool'}
DIRECTORIES = ['wine-cpu/run', 'wine-cpu/run/launcher', 'wine-cpu/run/lifecycle']
DIRECTORIES += [f'wine-cpu/run/{arch}' for arch in PROFILES]
DIRECTORIES += [f'wine-cpu/run/launcher/{arch}' for arch in PROFILES]
DIRECTORIES += [f'wine-cpu/run/launcher/{arch}/{phase}' for arch in PROFILES for phase in PHASES]
TREES = ['wine-cpu/run/build', 'wine-cpu/run/production', 'wine-cpu/run/lifecycle/results']
TREES += [f'wine-cpu/run/{arch}/results' for arch in PROFILES]
FILES = ['wine-cpu.log']
FILES += [f'wine-cpu/run/launcher/{arch}/compatdata/zluda-cuda-roots-v1.json' for arch in PROFILES]
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def stage(root, dest):
    if not root.is_absolute() or not dest.is_absolute() or dest.parent != root or dest.name == 'wine-cpu':
        raise ValueError('evidence destination must be a fresh sibling of wine-cpu under the absolute runner temp root')
    # Only the fixed evidence roots below are read; never walk runner temp or
    # launcher compatdata/tool trees. Use directory descriptors and O_NOFOLLOW
    # at every component, including file opens after directory enumeration.
    root_fd = os.open(root, DIR_FLAGS)
    record = {'status': 'complete', 'files': {}, 'missing': [], 'skipped': [], 'errors': []}

    def unavailable(relative, error):
        if error.errno == errno.ENOENT:
            record['missing'].append(relative)
        elif error.errno in (errno.ELOOP, errno.ENOTDIR):
            record['skipped'].append(relative)
        else:
            record['errors'].append({'path': relative, 'error': str(error)})

    def directory(relative):
        fd = os.dup(root_fd)
        try:
            for part in Path(relative).parts:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except OSError as error:
            os.close(fd)
            unavailable(relative, error)
            return None

    def copy_file(fd, name, relative):
        source_fd = None
        try:
            source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            if not stat.S_ISREG(os.fstat(source_fd).st_mode):
                record['skipped'].append(relative)
                return
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with os.fdopen(source_fd, 'rb') as source:
                source_fd = None
                with target.open('xb') as output:
                    while block := source.read(1024 * 1024):
                        output.write(block)
                        digest.update(block)
                        size += len(block)
            record['files'][relative] = {'sha256': digest.hexdigest(), 'size': size}
        except OSError as error:
            unavailable(relative, error)
        finally:
            if source_fd is not None:
                os.close(source_fd)

    def scan(fd, relative, recursive):
        try:
            names = sorted(os.listdir(fd))
        except OSError as error:
            unavailable(relative, error)
            return
        for name in names:
            child = relative + '/' + name
            if name in EXCLUDED:
                record['skipped'].append(child)
                continue
            try:
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                if stat.S_ISREG(mode):
                    if recursive or name.endswith(('.log', '.json')):
                        copy_file(fd, name, child)
                elif stat.S_ISDIR(mode) and recursive:
                    child_fd = os.open(name, DIR_FLAGS, dir_fd=fd)
                    try:
                        scan(child_fd, child, True)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    record['skipped'].append(child)
            except OSError as error:
                unavailable(child, error)

    try:
        dest.mkdir(mode=0o700)
        for relative in FILES:
            path = Path(relative)
            fd = directory(str(path.parent))
            if fd is not None:
                try:
                    copy_file(fd, path.name, relative)
                finally:
                    os.close(fd)
        for recursive, paths in [(False, DIRECTORIES), (True, TREES)]:
            for relative in paths:
                fd = directory(relative)
                if fd is not None:
                    try:
                        scan(fd, relative, recursive)
                    finally:
                        os.close(fd)
        record['status'] = 'incomplete' if record['errors'] else 'complete'
        (dest / 'STAGING.json').write_text(json.dumps(record, indent=2) + '\n')
    finally:
        os.close(root_fd)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--dest', type=Path, required=True)
    args = parser.parse_args()
    result = stage(args.root, args.dest)
    parser.exit(1 if result['errors'] else 0)
