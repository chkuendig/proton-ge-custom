#!/usr/bin/env python3
"""One-off BL2 diagnostic bridge; reuse attested B3 Wine, never qualify a release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

SOURCE = '47d838967e3742f757b7861aba705cf96dd8182d'
B3_ARCHIVE = '3b3328887d703faa54f8174810cd87810094ade95a44818cf2fce5d3b4ee6a7d'
B3_CAPSULE = '31c5c66c0976e23f53c862ea41321203cb776d0680ffbcfb3b9d1c90fcf7239c'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(argv, env=None):
    subprocess.run([str(arg) for arg in argv], env=env, check=True)


def prepare(source, work, archive):
    if subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() != SOURCE:
        raise ValueError('diagnostic source pin mismatch')
    if sha(archive) != B3_ARCHIVE:
        raise ValueError('B3 archive differs from the admitted source')
    work.mkdir(exist_ok=False)
    with tarfile.open(archive) as tar:
        # This archive is hash-pinned and previously inspected entry-by-entry.
        tar.extractall(work)
    tool = work / 'GE-Proton11-7-zluda'
    capsule = tool / 'sources/zluda-wine.tar.gz'
    if sha(capsule) != B3_CAPSULE:
        raise ValueError('B3 SDK source capsule mismatch')
    with tarfile.open(capsule) as tar:
        members = [m for m in tar if m.name.startswith('external/')]
        tar.extractall(work / 'capsule', members=members)
    external = work / 'capsule/external'
    record = json.loads((external / 'BRIDGE-BUILD.json').read_text())
    sdk = external / 'prepared-wine-sdk'
    (sdk / 'include').mkdir(exist_ok=True)
    (sdk / 'include-generated').mkdir(exist_ok=True)
    # Production uses declarations plus the SDK binary/provider receipt, not
    # Wine headers. The build script requires these directory placeholders.
    (work / 'DIAGNOSTIC.json').write_text(json.dumps({
        'qualification': 'PENDING diagnostic only; not a release or full B4 tool',
        'source_commit': SOURCE, 'base_proton': '0bda91adaba470495888a210d5b30715c337d0d2',
        'base_archive_sha256': B3_ARCHIVE, 'sdk_capsule_sha256': B3_CAPSULE,
        'abi_receipt_sha256': record['abi_receipt_sha256'],
        'source_date_epoch': subprocess.check_output(['git', '-C', str(source), 'show', '-s', '--format=%ct', 'HEAD'], text=True).strip(),
        'results': {}
    }, indent=2) + '\n')


def build(source, work):
    external = work / 'capsule/external'
    receipt = external / 'prepared-wine-abi.json'
    report = json.loads((work / 'DIAGNOSTIC.json').read_text())
    if sha(receipt) != report['abi_receipt_sha256']:
        raise ValueError('prepared SDK receipt mismatch')
    env = dict(os.environ, WINE_SDK_ROOT=str(external / 'prepared-wine-sdk'),
               ZLUDA_WINE_BUILD_ROOT=str(work / 'bridge'), ZLUDA_WINE_SOURCE_ID=SOURCE,
               SOURCE_DATE_EPOCH=report['source_date_epoch'], CARGO_HOME=str(work / 'cargo'),
               ZLUDA_WINE_ABI_RECEIPT=str(receipt),
               ZLUDA_WINE_ABI_RECEIPT_SHA256=report['abi_receipt_sha256'], JOBS='3')
    run(['python3', source / 'wine/zluda_wine/tests/check_production_inputs.py'], env)
    run(['bash', source / 'wine/zluda_wine/build-bridge.sh'], env)
    fixtures = work / 'fixtures'
    fixtures.mkdir()
    base = source / 'wine/zluda_wine/tests/abi'
    rows = [line.split('\t') for line in (source / 'wine/zluda_wine/api/wow64-backend-abi.tsv').read_text().splitlines()
            if line and not line.startswith('#')]
    (fixtures / 'wow64-native-contract.h').write_text(''.join(f'int {n}({a});\n' for n, a in rows))
    for suffix, backend in [('', 'backend.c'), ('-wow64', 'backend-wow64.c')]:
        run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared', '-pthread',
             base / backend, '-I', fixtures, '-Wl,-soname,libcuda.so.1', '-o', fixtures / ('libcuda' + suffix + '.so.1')])
    for triplet, probe in [('x86_64', 'probe'), ('i686', 'probe32')]:
        run([triplet + '-w64-mingw32-gcc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
             '-static-libgcc', base / (probe + '.c'), '-o', fixtures / (probe + '.exe')])


def test(source, work):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import wine_cpu
    tool = work / 'GE-Proton11-7-zluda'
    report_path = work / 'DIAGNOSTIC.json'
    report = json.loads(report_path.read_text())
    on_root = work / 'on-root'
    on_root.mkdir()
    for name in wine_cpu.cuda_package.MODULES:
        target = on_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / 'bridge/artifacts' / name, target)
    for arch, probe, backend in [('win64', 'probe.exe', 'libcuda.so.1'),
                                 ('wow64', 'probe32.exe', 'libcuda-wow64.so.1')]:
        directory = work / arch
        directory.mkdir()
        for name in ['tmp', 'runtime']:
            (directory / name).mkdir(mode=0o700)
        wine, server = [tool / 'files/bin' / name for name in ['wine', 'wineserver']]
        env = wine_cpu.environment(tool, directory, wine, server, arch)
        results = work / 'results' / arch
        run(['python3', source / 'wine/zluda_wine/tests/abi/run-fixtures.py', '--wine', wine,
             '--wineserver', server, '--winearch', arch, '--dll-dir', tool / 'files/lib/wine',
             '--on-root', on_root, '--off-root', tool / 'files/lib/wine-cuda/stub',
             '--backend', work / 'fixtures' / backend, '--probe', work / 'fixtures' / probe,
             '--prefix', directory / 'prefix', '--results', results], env)
        record = wine_cpu.check_result(results, arch, on_root)
        report['results'][arch] = {'qualification': record['qualification'], 'cases': len(record['cases'])}
        report_path.write_text(json.dumps(report, indent=2) + '\n')
    report['qualification'] = 'B3 SDK bridge CPU PE64+WoW64 passed; diagnostic only; GPU untested'
    report_path.write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'test'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.source, args.work, args.archive)
    else:
        globals()[args.action](args.source, args.work)
