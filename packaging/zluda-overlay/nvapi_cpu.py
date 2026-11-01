#!/usr/bin/env python3
"""Run cleaned NVAPI unit/dispatcher tests under packaged Wine, with no GPU."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil

from inputs import HERE, LOCK, git, sha256, verify_manifest
import wine_cpu
import cuda_package

DISABLES = ['NvAPI_CUDA_EnumComputeCapableByTopology', 'NvAPI_GPU_CudaEnumComputeCapableGpus',
            'nvapi_cuda_enumcomputecapablebytopology']


def admit(source, tool, fixtures):
    verify_manifest(fixtures)
    record = json.loads((fixtures / 'NVAPI-TESTS.json').read_text())
    if (record['proton'] != git(source, 'rev-parse', 'HEAD') or
            record['input_lock_sha256'] != sha256(HERE / 'inputs.lock.json') or
            record['sdk_image'] != LOCK['sdk_image'] or
            record['patches'] != LOCK['component_patches']['dxvk-nvapi'] or
            record['pe32_adapter_sha256'] != sha256(fixtures / 'PE32-ADAPTER.json')):
        raise ValueError('NVAPI test source/provenance mismatch')
    for name, digest in record['tests'].items():
        if sha256(fixtures / name) != digest:
            raise ValueError('NVAPI test executable mismatch')
    for name, digest in record['binaries'].items():
        if sha256(tool / name) != digest:
            raise ValueError('NVAPI unit build differs from assembled DLLs')
    return record


def result(log):
    text = log.read_text()
    match = re.search(r'All tests passed \((\d+) assertions? in (\d+) test cases?\)', text)
    if not match or any(int(value) <= 0 for value in match.groups()):
        raise ValueError('NVAPI tests did not execute/assert success: ' + str(log))
    return {'assertions': int(match[1]), 'cases': int(match[2]), 'log_sha256': sha256(log)}


def stage_graphics(tool, prefix, arch):
    # Match Proton's normal native graphics imports. A bare wineboot prefix
    # lacks that launcher setup; these unit tests still mock all GPU objects.
    pe_arch, system, machine = ('i386-windows', 'syswow64', 0x14c) if arch == 'wow64' else (
        'x86_64-windows', 'system32', 0x8664)
    record = {}
    for component, names in [('dxvk', ['d3d11', 'dxgi']), ('vkd3d-proton', ['d3d12', 'd3d12core'])]:
        for name in names:
            source = tool / 'files/lib/wine' / component / pe_arch / (name + '.dll')
            if cuda_package.pe_info(source, require_builtin=False)['machine'] != machine:
                raise ValueError('NVAPI graphics import architecture mismatch')
            target = prefix / 'drive_c/windows' / system / source.name
            target.unlink(missing_ok=True)
            shutil.copyfile(source, target)
            record[str(source.relative_to(tool))] = sha256(target)
    return record


def run(source, tool, fixtures, work):
    if work.exists():
        raise ValueError('NVAPI CPU output must be fresh')
    work.mkdir(parents=True)
    record = {'status': 'running', 'scope': 'mock topology/real dispatcher under packaged Wine; GPU/Deck unqualified',
              'profiles': {}}
    failure = None
    try:
        wine_cpu.admit_cpu_devices()
        wine, server = wine_cpu.admit_tool(source, tool)
        record['inputs'] = admit(source, tool, fixtures)
        for arch, executable in [('win64', 'nvapi64-tests.exe'), ('wow64', 'nvapi-tests.exe')]:
            directory = work / arch
            for name in ['tmp', 'runtime']:
                (directory / name).mkdir(parents=True, mode=0o700)
            env = wine_cpu.environment(tool, directory, wine, server, arch)
            for key in list(env):
                if key.startswith('DXVK_NVAPI_'):
                    env.pop(key)
            env['WINEDLLPATH'] = str(tool / 'files/lib/wine-cuda/stub') + ':' + str(tool / 'files/lib/wine')
            env['WINEDEBUG'] = '-all,err+module,err+loaddll'
            info = cuda_package.pe_info(fixtures / executable, require_builtin=False)
            if info['machine'] != (0x14c if arch == 'wow64' else 0x8664):
                raise ValueError('NVAPI test executable architecture mismatch')
            profile = {'status': 'running', 'pe_machine': info['machine'], 'cases': []}
            record['profiles'][arch] = profile
            try:
                wine_cpu.command([wine, 'wineboot.exe', '-u'], env, directory / 'bootstrap.log', 120)
                profile['graphics_imports'] = stage_graphics(tool, Path(env['WINEPREFIX']), arch)
                env['WINEDLLOVERRIDES'] = 'd3d11,dxgi,d3d12,d3d12core=n'
                for label, disabled in [('unit', None), *[(str(i), value) for i,value in enumerate(DISABLES)]]:
                    case = directory / label
                    case.mkdir()
                    case_env = dict(env)
                    if disabled:
                        case_env['DXVK_NVAPI_DISABLE_ENTRYPOINTS'] = disabled
                    args = ['PhysX private interface lookup'] if disabled else ['[@unit-tests]']
                    code = wine_cpu.command([wine, fixtures / executable, *args], case_env, case / 'test.log', 300)
                    item = dict(result(case / 'test.log'), mode=label, disabled=disabled, returncode=code)
                    if disabled is None and item['cases'] != (29 if arch == 'win64' else 14):
                        raise ValueError('NVAPI unit test inventory is incomplete')
                    item['cleanup'] = wine_cpu.cleanup_server(server, case_env, case)
                    if item['cleanup']['status'] != 'passed':
                        raise ValueError('NVAPI test server survived')
                    profile['cases'].append(item)
                profile['status'] = 'passed'
            finally:
                profile['cleanup'] = wine_cpu.cleanup_server(server, env, directory)
                if profile['cleanup']['status'] != 'passed':
                    raise ValueError('NVAPI outer cleanup failed')
        if any(len(p['cases']) != 4 for p in record['profiles'].values()):
            raise ValueError('NVAPI requires unit plus three isolated disable processes in both architectures')
    except BaseException as error:
        failure = error
        record['error'] = str(error)
    finally:
        record['status'] = 'failed' if failure else 'passed'
        (work / 'RESULT.json').write_text(json.dumps(record, indent=2) + '\n')
    if failure:
        raise failure
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['source', 'tool', 'fixtures', 'work']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.tool, args.fixtures, args.work)
