#!/usr/bin/env python3
"""Execute real PE64/PE32 NVAPI policy tests with mocked GPUs, in Wine."""
import argparse
import json
from pathlib import Path

from inputs import LOCK, sha256
import wine_cpu

REQUIRED = ['CudaEnumComputeCapableGpus (V1) returns OK',
            'CudaEnumComputeCapableGpus (V2) ignores uninitialized outputs',
            'CudaEnumComputeCapableGpus (V2) provides null-array storage',
            'ALLOW_OTHER_DRIVERS=0', 'ALLOW_OTHER_DRIVERS=1', 'All tests passed']


def run(tool, fixtures, work):
    wine_cpu.admit_cpu_devices()
    work.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'scope': 'Wine CPU mocks; no NVIDIA hardware equivalence',
              'qualification_patch': LOCK['nvapi_qualification'], 'architectures': {}}
    wine, server = tool / 'files/bin/wine', tool / 'files/bin/wineserver'
    try:
        for arch, bits in [('win64', '64'), ('wow64', '32')]:
            directory = work / ('work' + bits)
            for name in ['tmp', 'runtime']:
                (directory / name).mkdir(parents=True, mode=0o700)
            env = wine_cpu.environment(tool, directory, wine, server, arch)
            env.update(WINEDLLPATH=str(tool / 'files/lib/wine-cuda/stub'),
                       WINEDLLOVERRIDES='mscoree,mshtml=', PROTON_ENABLE_ZLUDA='0')
            env.pop('DXVK_NVAPI_ALLOW_OTHER_DRIVERS', None)
            binary = fixtures / ('nvapi-tests' + bits + '.exe')
            result = {'status': 'running', 'executable_sha256': sha256(binary)}
            report['architectures'][arch] = result
            try:
                wine_cpu.command([wine, 'wineboot', '-i'], env, work / ('boot' + bits + '.log'), 180)
                logfile = work / ('nvapi' + bits + '.log')
                wine_cpu.command([wine, binary, '[.sysinfo]', '--success', '--reporter', 'console'],
                                 env, logfile, 300)
                text = logfile.read_text(errors='replace')
                if not all(value in text for value in REQUIRED):
                    raise ValueError('NVAPI flag/version/poisoned-output coverage missing: ' + bits)
                result.update(status='passed', required_observations=REQUIRED, log_sha256=sha256(logfile))
            finally:
                result['cleanup'] = wine_cpu.cleanup_server(server, env, work / ('cleanup' + bits))
                if result['cleanup']['status'] != 'passed':
                    raise ValueError('NVAPI Wine server cleanup failed: ' + bits)
        report['status'] = 'passed'
    finally:
        (work / 'RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['tool', 'fixtures', 'work']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.tool, args.fixtures, args.work)
