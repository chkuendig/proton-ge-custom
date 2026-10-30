#!/usr/bin/env python3
"""Pinned downloads, checkout admission and full SHA-256 manifests (stdlib only)."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / 'inputs.lock.json').read_text())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args]).decode().strip()


def request(url, method='GET'):
    return urllib.request.urlopen(urllib.request.Request(
        url, method=method, headers={'User-Agent': 'zluda-overlay-pins/1'}), timeout=60)


def download(item, cache):
    target = cache / item.get('filename', item['url'].rsplit('/', 1)[1])
    if not target.exists() or sha256(target) != item['sha256']:
        partial = target.with_name(target.name + '.partial')
        with request(item['url']) as source, partial.open('wb') as out:
            while block := source.read(1024 * 1024):
                out.write(block)
        if sha256(partial) != item['sha256']:
            partial.unlink()
            raise ValueError('checksum mismatch: ' + item['id'])
        partial.replace(target)
    return target


def resolve(cache):
    """HEAD every payload; validate hashes against small authoritative metadata."""
    image, digest = LOCK['sdk_image'].split('@', 1)
    registry, repository = image.split('/', 1)
    auth = ('https://gitlab.steamos.cloud/jwt/auth?service=container_registry'
            '&scope=repository:' + repository + ':pull')
    with request(auth) as response:
        token = json.load(response)['token']
    url = 'https://' + registry + '/v2/' + repository + '/manifests/' + digest
    headers = {'Authorization': 'Bearer ' + token,
               'Accept': 'application/vnd.docker.distribution.manifest.v2+json'}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
        assert 'sha256:' + hashlib.sha256(response.read()).hexdigest() == digest
    print('resolved SDK manifest', digest, flush=True)
    index = download(dict(LOCK['rocm_index'], id='Packages'), cache)
    records = {}
    for stanza in gzip.decompress(index.read_bytes()).decode().split('\n\n'):
        record = dict(line.split(': ', 1) for line in stanza.splitlines()
                      if ': ' in line and not line.startswith(' '))
        if 'Filename' in record:
            records[record['Filename']] = record
    for item in LOCK['downloads']:
        assert re.fullmatch('[0-9a-f]{64}', item['sha256']), item['id']
        with request(item['url'], 'HEAD') as response:
            length = response.headers.get('Content-Length')
            if length and 'size' in item:
                assert int(length) == item['size'], item['id']
        if item.get('group') == 'rocm':
            record = records[item['url'].split('/7.2.4/', 1)[1]]
            assert record['SHA256'] == item['sha256'], item['id']
            assert record['Version'] == item['version'], item['id']
            assert int(record['Size']) == item['size'], item['id']
        elif item['id'] == 'ge':
            with request(item['digest_url']) as response:
                release = json.load(response)
            asset = next(a for a in release['assets'] if a['browser_download_url'] == item['url'])
            assert asset['digest'] == 'sha256:' + item['sha256']
        elif item['id'] == 'rust' or item['id'].startswith('rust-std-'):
            with request(item['url'] + '.sha256') as response:
                assert response.read().decode().split()[0] == item['sha256']
        else:
            download(item, cache)  # libnuma source is less than 500 KiB
        print('resolved', item['id'], item['sha256'], flush=True)


def check_tree(root):
    """Every gitlink in an admitted component's recursive closure must match."""
    for record in git(root, 'ls-tree', '-r', 'HEAD').splitlines():
        meta, path = record.split('\t', 1)
        mode, kind, oid = meta.split()
        if mode != '160000':
            continue
        child = root / path
        if not (child / '.git').exists():
            raise ValueError('uninitialized submodule: ' + str(child))
        if git(child, 'rev-parse', 'HEAD') != oid:
            raise ValueError('gitlink mismatch: ' + str(child))
        if git(child, 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('dirty component: ' + str(child))
        check_tree(child)


def components_for_part(part):
    if part == 'all':
        return list(dict.fromkeys(LOCK['build_parts']['zluda'] + LOCK['build_parts']['bridges']))
    return LOCK['build_parts'][part]


def check_bridge_sources(root):
    """Full GE prep intentionally patches other components and main-tree files.

    The independent Wine bridge consumes the exact clean ZLUDA checkout and
    committed lock/patch inputs, rather than those unrelated prepared trees.
    """
    git(root, 'merge-base', '--is-ancestor', LOCK['proton_base'], 'HEAD')
    path = 'packaging/zluda-overlay/inputs.lock.json'
    if (root / path).read_bytes() != subprocess.check_output(['git', '-C', str(root), 'show', 'HEAD:' + path]):
        raise ValueError('dirty bridge input lock')
    child = root / 'zluda'
    pin = LOCK['components']['zluda']
    if git(root, 'rev-parse', 'HEAD:zluda') != pin or git(child, 'rev-parse', 'HEAD') != pin:
        raise ValueError('locked Wine bridge gitlink mismatch')
    if git(child, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('dirty Wine bridge component')
    check_tree(child)
    bridge = LOCK['wine_bridge']
    if (git(child, 'rev-parse', 'HEAD:' + bridge['path']) != bridge['tree'] or
            sha256(child / bridge['path'] / 'Cargo.lock') != bridge['cargo_lock_sha256']):
        raise ValueError('locked Wine bridge tree/lock mismatch')
    from component_patches import series
    for name in LOCK.get('component_patches', {}):
        series(root, name, LOCK)
    print('checked immutable ZLUDA bridge and committed patch inputs')


def check_sources(root, part):
    git(root, 'merge-base', '--is-ancestor', LOCK['proton_base'], 'HEAD')
    if git(root, 'status', '--porcelain', '--untracked-files=no', '--ignore-submodules=all'):
        raise ValueError('dirty Proton checkout')
    names = components_for_part(part)
    for name in names:
        child = root / name
        expected = git(root, 'rev-parse', 'HEAD:' + name)
        if expected != LOCK['components'][name] or git(child, 'rev-parse', 'HEAD') != expected:
            raise ValueError('locked gitlink mismatch: ' + name)
        if git(child, 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('dirty component: ' + name)
        check_tree(child)
        if name in LOCK.get('component_patches', {}):
            from component_patches import series
            series(root, name, LOCK)
    if part in ('bridges', 'all'):
        bridge = LOCK['wine_bridge']
        if git(root / 'zluda', 'rev-parse', 'HEAD:' + bridge['path']) != bridge['tree']:
            raise ValueError('locked Wine bridge tree mismatch')
        if sha256(root / 'zluda' / bridge['path'] / 'Cargo.lock') != bridge['cargo_lock_sha256']:
            raise ValueError('locked Wine bridge Cargo.lock mismatch')
    if part in ('zluda', 'all'):
        assert git(root / 'zluda/ext/llvm-project', 'rev-parse', 'HEAD') == LOCK['llvm']
        assert sha256(root / 'zluda/Cargo.lock') == LOCK['cargo_lock_sha256']
        for name, item in LOCK['lfs'].items():
            path = root / 'zluda' / name
            assert path.stat().st_size == item['size'] and sha256(path) == item['sha256'], name
    print('checked recursive gitlinks and locked inputs:', ', '.join(names))


def wine_base(root):
    """Select GE by immutable lock entries, even when no release tags exist."""
    base = LOCK['ge_source']
    if git(root, 'rev-parse', base + '^{commit}') != base:
        raise ValueError('GE source must be a full locked commit ID')
    git(root, 'merge-base', '--is-ancestor', base, 'HEAD')
    item = next(item for item in LOCK['downloads'] if item['id'] == 'ge')
    archive = item['url'].rsplit('/', 1)[1]
    suffix = '-x86_64.tar.gz'
    if not archive.startswith('GE-Proton') or not archive.endswith(suffix):
        raise ValueError('unexpected locked GE archive name: ' + archive)
    return base, archive[:-len(suffix)], archive


def baseline_links(root):
    path = root / 'provenance/GE-TEMPLATE-LINKS.json'
    return json.loads(path.read_text()) if path.exists() else {}


def manifest(root):
    # Hash every regular file. SYMLINKS.json covers link targets, including
    # directory links and any unchanged external links in GE's prefix template.
    # SHA256SUMS beside the archive covers MANIFEST itself.
    root = root.resolve()
    links = {p.relative_to(root).as_posix(): os.readlink(p)
             for p in root.rglob('*') if p.is_symlink()}
    baseline = baseline_links(root)
    for name, target in links.items():
        if not (root / name).resolve().is_relative_to(root) or not (root / name).exists():
            if baseline.get(name) != target:
                raise ValueError('invalid payload symlink: ' + name)
    (root / 'SYMLINKS.json').write_text(json.dumps(links, sort_keys=True, indent=2) + '\n')
    lines = []
    for path in sorted(root.rglob('*')):
        if path == root / 'MANIFEST' or path.is_symlink() or path.is_dir():
            continue
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError('invalid payload link/file: ' + str(path))
        name = path.relative_to(root).as_posix()
        if '\n' in name or '\\' in name:
            raise ValueError('unsupported manifest filename: ' + name)
        lines.append(sha256(path) + '  ' + name + '\n')
    (root / 'MANIFEST').write_text(''.join(lines))


def verify_manifest(root):
    links = {p.relative_to(root).as_posix(): os.readlink(p)
             for p in root.rglob('*') if p.is_symlink()}
    if links != json.loads((root / 'SYMLINKS.json').read_text()):
        raise ValueError('symlink inventory mismatch')
    baseline = baseline_links(root)
    for name, target in links.items():
        if not (root / name).resolve().is_relative_to(root.resolve()) or not (root / name).exists():
            if baseline.get(name) != target:
                raise ValueError('escaping/broken payload symlink: ' + name)
    expected = set()
    for line in (root / 'MANIFEST').read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('manifest path escapes root: ' + name)
        if sha256(path) != digest:
            raise ValueError('manifest mismatch: ' + name)
        expected.add(name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
              if not p.is_symlink() and not p.is_dir() and p != root / 'MANIFEST'}
    if expected != actual:
        raise ValueError('manifest file set mismatch')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['fetch', 'resolve', 'check', 'bridge-check', 'manifest', 'verify', 'wine-base', 'checkout-paths'])
    parser.add_argument('directory', type=Path)
    parser.add_argument('--part', choices=['all', 'zluda', 'bridges'], default='all')
    parser.add_argument('--group', choices=['all', 'zluda', 'bridges', 'ge'], default='all')
    args = parser.parse_args()
    if args.command == 'check':
        check_sources(args.directory, args.part)
    elif args.command == 'bridge-check':
        check_bridge_sources(args.directory)
    elif args.command == 'wine-base':
        print(*wine_base(args.directory))
    elif args.command == 'checkout-paths':
        print(*components_for_part(args.part))
    elif args.command == 'manifest':
        manifest(args.directory)
    elif args.command == 'verify':
        verify_manifest(args.directory)
    else:
        args.directory.mkdir(parents=True, exist_ok=True)
        if args.command == 'resolve':
            resolve(args.directory)
        else:
            for item in LOCK['downloads']:
                ids = {'zluda': ['rust', 'numactl'], 'bridges': ['ge', 'rust', 'rust-std-x86_64-pc-windows-gnu', 'rust-std-i686-pc-windows-gnu'], 'ge': ['ge']}
                if (args.group == 'all' or item['id'] in ids[args.group]
                        or (args.group == 'zluda' and item.get('group') == 'rocm')):
                    print(item['id'], download(item, args.directory), flush=True)


if __name__ == '__main__':
    main()
