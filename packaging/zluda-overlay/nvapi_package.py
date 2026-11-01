#!/usr/bin/env python3
"""Admit and package exact cleaned NVAPI production sources and CPU tests."""
import argparse
import json
import re
from pathlib import Path
import shutil

from inputs import HERE, LOCK, git, manifest, sha256, verify_manifest
import component_patches

BINARIES = ['files/lib/wine/nvapi/x86_64-windows/nvapi64.dll',
            'files/lib/wine/nvapi/x86_64-windows/nvofapi64.dll',
            'files/lib/wine/nvapi/i386-windows/nvapi.dll']


def adapt_pe32(source, dest, receipt):
    # Upstream disables tests on x86. Only the private test copy changes;
    # production and the source archive retain the exact seven-patch tree.
    shutil.copytree(source, dest, symlinks=True)
    root = dest / 'meson.build'
    original = root.read_text()
    old = "if enable_tests and dxvk_cpu_family == 'x86_64' # compiling tests for x86 fails, dunno why..."
    if original.count(old) != 1:
        raise ValueError('upstream PE32 test gate changed')
    root.write_text(original.replace(old, 'if enable_tests'))
    path = dest / 'tests/meson.build'
    text = path.read_text()
    start, end = text.index('nvapi_tests_src = files(['), text.index("target_name = 'nvapi'+")
    keep = ['nvapi_main.cpp', 'nvapi/mock_factory.cpp', 'nvapi/default_test_environment.cpp',
            'nvapi/extended_test_environment.cpp', 'nvapi_sysinfo.cpp', 'nvapi_sysinfo_topo.cpp', 'nvapi_sysinfo_cuda.cpp']
    path.write_text(text[:start] + 'nvapi_tests_src = files([\n' +
                    ''.join("  '" + name + "',\n" for name in keep) + '])\n\n' + text[end:])
    # x64 has one calling convention; x86 COM methods require stdcall. Use
    # Trompeloeil's existing COM macros, only in these COM mock headers.
    mock_headers = ['tests/mocks/dxgi_mocks.h', 'tests/mocks/d3d12_mocks.h']
    private_interface = source / 'src/interfaces/vkd3d-proton_interfaces.h'
    private_cdecl = re.findall(r'\bvirtual\s+\w+\s+(\w+)\s*\(', private_interface.read_text())
    for name in mock_headers:
        path = dest / name
        content = path.read_text()
        content = re.sub(r'\bMAKE_MOCK(\d+)\(', r'MAKE_STDMETHOD_MOCK\1(', content)
        content = re.sub(r'\bIMPLEMENT_MOCK(\d+)\(', r'IMPLEMENT_STDMETHOD_MOCK\1(', content)
        if name.endswith('d3d12_mocks.h'):
            # vkd3d's private extension classes deliberately mix COM stdcall
            # with ordinary C++ methods. Preserve each declared convention.
            for method in private_cdecl:
                content = re.sub(r'\bIMPLEMENT_STDMETHOD_MOCK(\d+)\(' + re.escape(method) + r'\)',
                                 r'IMPLEMENT_MOCK\1(' + method + ')', content)
        path.write_text(content)
    receipt.write_text(json.dumps({'scope': 'PE32 topology and real QueryInterface dispatcher tests',
        'production_tree': LOCK['component_patches']['dxvk-nvapi']['prepared_tree'],
        'changes': {name: {'before': sha256(source / name), 'after': sha256(dest / name)}
                    for name in ['meson.build', 'tests/meson.build', *mock_headers]},
        'test_sources': keep, 'private_cdecl_methods': private_cdecl,
        'private_interface_sha256': sha256(private_interface),
        'com_convention': 'standard COM stdcall; private vkd3d declarations preserved; MinGW return-pointer declarations unchanged'}, indent=2) + '\n')


def receipt(source, dest, tests):
    record = {'format': 'nvapi-provider-series-1', 'proton': git(source, 'rev-parse', 'HEAD'),
              'input_lock_sha256': sha256(HERE / 'inputs.lock.json'), 'sdk_image': LOCK['sdk_image'],
              'component': LOCK['components']['dxvk-nvapi'],
              'prepared_source_sha256': sha256(dest / 'sources/dxvk-nvapi-prepared.tar.gz'),
              'patches': LOCK['component_patches']['dxvk-nvapi'],
              'binaries': {name: sha256(dest / name) for name in BINARIES},
              'recipes': {name: sha256(HERE / name) for name in ['build-nvapi.sh', 'nvapi_package.py', 'nvapi-provider-probe.cpp', 'nvapi-provider-backend.c']}}
    (dest / 'provenance/NVAPI-BUILD.json').write_text(json.dumps(record, indent=2) + '\n')
    test_record = dict(record, tests={name: sha256(tests / name) for name in ['nvapi64-tests.exe', 'nvapi-tests.exe', 'provider-64.exe', 'provider-32.exe', 'provider-backend.so']},
                       pe32_adapter_sha256=sha256(tests / 'PE32-ADAPTER.json'))
    (tests / 'NVAPI-TESTS.json').write_text(json.dumps(test_record, indent=2) + '\n')
    manifest(tests)
    manifest(dest)


def admit(source, overlay):
    verify_manifest(overlay)
    record = json.loads((overlay / 'provenance/NVAPI-BUILD.json').read_text())
    if (record['proton'] != git(source, 'rev-parse', 'HEAD') or
            record['input_lock_sha256'] != sha256(HERE / 'inputs.lock.json') or
            record['patches'] != LOCK['component_patches']['dxvk-nvapi'] or
            record['sdk_image'] != LOCK['sdk_image'] or
            record['prepared_source_sha256'] != sha256(overlay / 'sources/dxvk-nvapi-prepared.tar.gz') or
            record['binaries'] != {name: sha256(overlay / name) for name in BINARIES}):
        raise ValueError('NVAPI replacement provenance mismatch')
    component_patches.series(source, 'dxvk-nvapi')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('action', choices=['adapt-pe32', 'receipt'])
    parser.add_argument('first', type=Path)
    parser.add_argument('second', type=Path)
    parser.add_argument('third', type=Path)
    args = parser.parse_args()
    (adapt_pe32 if args.action == 'adapt-pe32' else receipt)(args.first, args.second, args.third)
