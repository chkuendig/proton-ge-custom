#!/usr/bin/env python3
"""Rebuild the extracted triad against B5's admitted SDK and compare shipping bytes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tarfile

from inputs import LOCK, bridge_root, git, sha256
import source_archive

B5_ARCHIVE_SHA256 = '9878f12e96f4a02ef35ad36313f7aa8c3dfbe302e399252a8254e66d18728cdb'
B5_MODULES = {
    'x86_64-windows/nvcuda.dll': '451aa80f499096159d7229579bb86d121619c04bb7d986f314113aab4c88dee1',
    'i386-windows/nvcuda.dll': 'd007765335b3bd8659d99397e62d3ecda40dd8786dc354ecaf2dbe90887c8a3f',
    'x86_64-unix/nvcuda.so': '1b13a959f06f713350fb5665527b5e876eca3c84eccbd7989fae0b4e477dfc09',
}


def prepare(archive, work):
    if sha256(archive) != B5_ARCHIVE_SHA256:
        raise ValueError('B5 admitted product archive differs')
    work.mkdir(parents=True, exist_ok=False)
    capsule = work / 'b5-source.tar.gz'
    matches = 0
    with tarfile.open(archive, 'r|gz') as product:
        for member in product:
            if member.isfile() and member.name.endswith('/sources/zluda-wine.tar.gz'):
                matches += 1
                with capsule.open('wb') as output:
                    import shutil
                    shutil.copyfileobj(product.extractfile(member), output)
    if matches != 1:
        raise ValueError('B5 source capsule missing or ambiguous')
    root = work / 'b5-source'
    root.mkdir()
    with tarfile.open(capsule) as source:
        for member in source:
            target = (root / member.name).resolve()
            if (not target.is_relative_to(root.resolve()) or
                    not (member.isfile() or member.isdir() or member.issym()) or
                    member.issym() and not (target.parent / member.linkname).resolve().is_relative_to(root.resolve())):
                raise ValueError('unsafe B5 source capsule member')
        source.extractall(root)
    return root


def run(source, work):
    bridge = bridge_root(source)
    backend = source / 'zluda'
    pin = LOCK['components']['zluda-wine']
    if git(bridge, 'rev-parse', 'HEAD') != pin or git(backend, 'rev-parse', 'HEAD') != LOCK['components']['zluda']:
        raise ValueError('extraction source pins differ from lock')
    env = source_archive.extracted_environment(work / 'b5-source', os.environ)
    # The shipping capsule deliberately excludes Wine headers: the production
    # bridge reads only the attested binary provider/tools. The legacy builder
    # still checks these SDK layout directories, without consuming their files.
    for name in ['include', 'include-generated']:
        (Path(env['WINE_SDK_ROOT']) / name).mkdir(exist_ok=True)
    env.update(ZLUDA_WINE_SOURCE_ID=pin, ZLUDA_WINE_BACKEND_SOURCE=str(backend),
               SOURCE_DATE_EPOCH=LOCK['wine_bridge']['source_date_epoch'],
               ZLUDA_WINE_BUILD_ROOT='/work/build/zluda-wine', JOBS='3')
    build = Path(env['ZLUDA_WINE_BUILD_ROOT'])
    record = {'go_ref': 'e1c6a6e3-20261104-cleanup-p2', 'status': 'running',
              'backend': LOCK['components']['zluda'], 'bridge': pin,
              'B5_product_sha256': B5_ARCHIVE_SHA256, 'modules': {}}
    try:
        subprocess.run(['bash', str(bridge / 'wine/zluda_wine/build-bridge.sh')], env=env, check=True)
        for name, expected in B5_MODULES.items():
            actual = sha256(build / 'artifacts' / name)
            record['modules'][name] = {'B5': expected, 'candidate': actual, 'identical': actual == expected}
        # Generate the capsule even on a byte mismatch, to retain regeneration evidence.
        original = dict(os.environ)
        try:
            os.environ.update(env)
            record['source_capsule'] = source_archive.create(
                bridge, build / 'target', work / 'component-source.tar.gz')
            record['offline_triad'] = source_archive.rebuild(
                work / 'component-source.tar.gz', work / 'offline-rebuild')
        finally:
            os.environ.clear()
            os.environ.update(original)
        if not all(item['identical'] for item in record['modules'].values()):
            raise ValueError('extracted shipping module bytes differ from B5; no identity exemption')
        record['status'] = 'passed'
    except BaseException as error:
        record.update(status='failed', error=str(error))
        raise
    finally:
        (work / 'EXTRACTION-PROOF.json').write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.archive, args.work)
    else:
        run(args.source, args.work)
