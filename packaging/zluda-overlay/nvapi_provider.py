#!/usr/bin/env python3
"""CPU software-Vulkan experiment with production NVAPI/nvcuda, no mocked adapter.

An unavailable DXVK adapter or missing PCI bus info is reported as unqualified.
Once an adapter with PCI bus info exists, all positive and negative checks must pass.
"""
import argparse
import json
import os
from pathlib import Path
import shutil

from inputs import sha256
import nvapi_cpu
import wine_cpu


def windows(path):
    return 'Z:' + str(path).replace('/', '\\')


def run(source, tool, fixtures, work):
    if work.exists():
        raise ValueError('provider work must be fresh')
    work.mkdir(parents=True)
    wine_cpu.admit_cpu_devices()
    wine, server = wine_cpu.admit_tool(source, tool)
    inputs = nvapi_cpu.admit(source, tool, fixtures)
    record = {'scope': 'production NVAPI + production nvcuda + fake native CUDA backend, CPU Vulkan adapter',
              'inputs': inputs, 'profiles': {}, 'status': 'running'}
    failure = None
    try:
        for arch, bits, pe, dll in [('win64',64,'x86_64-windows','nvapi64.dll'),
                                   ('wow64',32,'i386-windows','nvapi.dll')]:
            profile = {'cases': []}
            record['profiles'][arch] = profile
            for mode, control in [('off','match'), ('on','match'), ('on','mismatch')]:
                case = work / arch / (mode + '-' + control)
                for name in ['tmp','runtime']:
                    (case / name).mkdir(parents=True, mode=0o700)
                env = wine_cpu.environment(tool, case, wine, server, arch)
                for key in list(env):
                    if key.startswith('DXVK_NVAPI_'):
                        env.pop(key)
                env.update(DISPLAY=os.environ['DISPLAY'], DXVK_NVAPI_ALLOW_OTHER_DRIVERS='1',
                           WINEDLLPATH=str(tool / 'files/lib/wine-cuda' / ('zluda' if mode=='on' else 'stub')) + ':' + str(tool / 'files/lib/wine'),
                           WINEDLLOVERRIDES='nvcuda=b;dxgi,d3d11,d3d12,d3d12core=n;winemenubuilder.exe=d',
                           PROTON_ENABLE_ZLUDA='1' if mode=='on' else '0',
                           ZLUDA_WINE_BACKEND_PATH=str(fixtures / 'provider-backend.so'),
                           NVAPI_PROVIDER_PCI=str(case / 'pci.bin'), NVAPI_PROVIDER_AUDIT=str(case / 'backend.log'),
                           DXVK_LOG_PATH=str(case), DXVK_NVAPI_LOG_PATH=str(case))
                item = {'mode': mode, 'control': control}
                profile['cases'].append(item)
                try:
                    wine_cpu.command([wine,'wineboot.exe','-u'],env,case/'bootstrap.log',180)
                    item['graphics'] = nvapi_cpu.stage_graphics(tool,Path(env['WINEPREFIX']),arch)
                    code = wine_cpu.command([wine,fixtures/f'provider-{bits}.exe',windows(case/'pci.bin'),
                        windows(tool/'files/lib/wine/nvapi'/pe/dll),mode,control],env,case/'probe.log',180,accepted=(0,77))
                    item.update(returncode=code,log_sha256=sha256(case/'probe.log'))
                    if code==77:
                        item['status']='unavailable'
                        profile['status']='unqualified: software Vulkan adapter/PCI info unavailable'
                        break
                    if 'PASS provider topology and PCI' not in (case/'probe.log').read_text():
                        raise ValueError('provider executable did not reach its verdict')
                    audit=(case/'backend.log').read_text().splitlines() if (case/'backend.log').exists() else []
                    item['backend_events']=audit
                    if mode=='on' and not {'backend-loaded','cuInit','cuDeviceGetCount','cuDeviceGet','cuDeviceGetAttribute'} <= set(audit):
                        raise ValueError('real native CUDA provider path was not exercised')
                    if mode=='off' and audit:
                        raise ValueError('stub control loaded CUDA backend')
                    item['status']='passed'
                finally:
                    item['cleanup']=wine_cpu.cleanup_server(server,env,case)
                    if item['cleanup']['status']!='passed':
                        raise ValueError('provider Wine server survived')
            else:
                profile['status']='passed'
        record['status']='passed' if all(p['status']=='passed' for p in record['profiles'].values()) else 'unqualified'
    except BaseException as error:
        failure=error
        record.update(status='failed',error=str(error))
    finally:
        (work/'RESULT.json').write_text(json.dumps(record,indent=2)+'\n')
    if failure:
        raise failure
    print('Provider CPU result:',record['status'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    for name in ['source','tool','fixtures','work']:
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    run(args.source,args.tool,args.fixtures,args.work)
