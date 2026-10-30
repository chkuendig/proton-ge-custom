#!/usr/bin/env python3
"""Package SDK-built CPU fixtures and admit them before hosted execution."""
import argparse
import gzip
import io
import json
import re
from pathlib import Path
import shutil
import tarfile

from inputs import HERE, LOCK, git, manifest, sha256, verify_manifest

REQUIRED = {'probe.exe', 'probe32.exe', 'libcuda.so.1', 'libcuda-wow64.so.1',
            'lifecycle.exe', 'lifecycle-helper.dll', 'transition.exe'}


def check_source_hashes(source, build):
    base = source / 'zluda' / LOCK['wine_bridge']['path'] / 'tests/abi'
    lines = (build / 'SOURCE-SHA256SUMS').read_text().splitlines()
    if not lines:
        raise ValueError('CPU fixture source hash inventory is empty')
    seen = set()
    for line in lines:
        digest, name = line.split(None, 1)
        if name in seen or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid/duplicate CPU fixture source hash')
        seen.add(name)
        path = (base / name).resolve()
        if not path.is_relative_to((source / 'zluda').resolve()) or sha256(path) != digest:
            raise ValueError('CPU fixture source differs from pinned checkout: ' + name)


def package(source, build, sdk_receipt, dest):
    if dest.exists() or not REQUIRED <= {p.name for p in build.iterdir() if p.is_file()}:
        raise ValueError('CPU fixture output must be fresh and contain every required binary')
    check_source_hashes(source, build)
    shutil.copyfile(sdk_receipt, build / 'WINE-SDK.json')
    manifest(build)
    dest.mkdir(parents=True)
    archive = dest / 'cpu-fixtures.tar.gz'
    with archive.open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w|') as tar:
            for file in sorted(build.rglob('*')):
                if file.is_symlink():
                    raise ValueError('CPU fixture artifacts must not contain symlinks')
                if file.is_file():
                    data = file.read_bytes()
                    member = tarfile.TarInfo(file.relative_to(build).as_posix())
                    member.size, member.mode = len(data), file.stat().st_mode & 0o777
                    tar.addfile(member, io.BytesIO(data))
    record = {'format': 'pinned-sdk-cpu-fixtures-1', 'proton': git(source, 'rev-parse', 'HEAD'),
              'source_pin': LOCK['components']['zluda'], 'sdk_image': LOCK['sdk_image'],
              'input_lock_sha256': sha256(HERE / 'inputs.lock.json'),
              'manifest_sha256': sha256(build / 'MANIFEST'), 'archive_sha256': sha256(archive),
              'wine_sdk_receipt_sha256': sha256(sdk_receipt),
              'qualification': 'SDK compile evidence only; Wine/Proton execution required'}
    (dest / 'cpu-fixtures.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def admit(source, directory, dest):
    record = json.loads((directory / 'cpu-fixtures.json').read_text())
    archive = directory / 'cpu-fixtures.tar.gz'
    if (record.get('format') != 'pinned-sdk-cpu-fixtures-1' or
            record.get('proton') != git(source, 'rev-parse', 'HEAD') or
            record.get('source_pin') != LOCK['components']['zluda'] or
            record.get('sdk_image') != LOCK['sdk_image'] or
            record.get('input_lock_sha256') != sha256(HERE / 'inputs.lock.json') or
            record.get('archive_sha256') != sha256(archive)):
        raise ValueError('CPU fixture provenance/archive mismatch')
    if dest.exists():
        raise ValueError('CPU fixture extraction requires a fresh directory')
    dest.mkdir()
    with tarfile.open(archive) as tar:
        for member in tar:
            if (not (member.isfile() or member.isdir()) or
                    not (dest / member.name).resolve().is_relative_to(dest.resolve())):
                raise ValueError('CPU fixture archive contains an unsafe member')
        tar.extractall(dest)
    if (sha256(dest / 'MANIFEST') != record['manifest_sha256'] or
            sha256(dest / 'WINE-SDK.json') != record['wine_sdk_receipt_sha256']):
        raise ValueError('CPU fixture manifest/SDK receipt mismatch')
    verify_manifest(dest)
    if not all((dest / name).is_file() for name in REQUIRED):
        raise ValueError('CPU fixture archive is incomplete')
    check_source_hashes(source, dest)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--sdk-receipt', type=Path, required=True)
    parser.add_argument('--dest', type=Path, required=True)
    args = parser.parse_args()
    package(args.source, args.build, args.sdk_receipt, args.dest)
