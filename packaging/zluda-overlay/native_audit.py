#!/usr/bin/env python3
"""Validate native Cargo link outputs against the private SDK before LLVM."""
import argparse
import json
from pathlib import Path

from inputs import sha256

AMD_PACKAGES = ['hip_runtime-sys', 'hipblaslt-sys', 'hipfft-sys', 'miopen-sys',
                'rocblas-sys', 'rocm_smi-sys', 'rocsparse-sys']


def audit(target, sdk, library_path, legacy=Path('/opt/rocm/lib')):
    if legacy.exists() and legacy.resolve() != (sdk / 'lib').resolve():
        raise ValueError('unexpected /opt/rocm/lib could override the pinned private SDK')
    records = {}
    for package in ['highs-sys', *AMD_PACKAGES]:
        outputs = sorted((target / 'build').glob(package + '-*/output'))
        if not outputs:
            raise ValueError('missing native build output: ' + package)
        entries = []
        for output in outputs:
            lines = output.read_text().splitlines()
            searches = [Path(line.split('native=', 1)[1]) for line in lines
                        if line.startswith('cargo:rustc-link-search=native=')]
            libs = [line.split('cargo:rustc-link-lib=', 1)[1] for line in lines
                    if line.startswith('cargo:rustc-link-lib=')]
            entry = {'output': str(output), 'searches': list(map(str, searches)), 'libraries': libs}
            if package == 'highs-sys':
                archives = [path / 'libhighs.a' for path in searches if (path / 'libhighs.a').is_file()]
                if not archives or 'static=highs' not in libs:
                    raise ValueError('HiGHS static archive absent from emitted native search paths')
                for archive in archives:
                    if not archive.resolve().is_relative_to(target.resolve()):
                        raise ValueError('HiGHS archive outside the private Cargo output')
                    with archive.open('rb') as stream:
                        if stream.read(8) != b'!<arch>\n':
                            raise ValueError('invalid HiGHS static archive: ' + str(archive))
                entry['archives'] = {str(path): sha256(path) for path in archives}
            else:
                # hipfft's default link feature must stay disabled just as it is
                # for cuda_types/zluda_common in the final zluda invocation.
                if package == 'hipfft-sys' and any('hipfft' in lib for lib in libs):
                    raise ValueError('unexpected hipfft linking in the CUDA type-only profile')
                resolved = {}
                for lib in libs:
                    name = lib.removeprefix('dylib=')
                    candidates = [path / ('lib' + name + '.so') for path in [*searches, *library_path]]
                    found = next((path for path in candidates if path.is_file()), None)
                    if found is None:
                        raise ValueError('unresolved native library: ' + name)
                    with found.open('rb') as stream:
                        if stream.read(4) != b'\x7fELF':
                            raise ValueError('invalid native library: ' + str(found))
                    resolved[name] = str(found.resolve())
                entry['resolved'] = resolved
            entries.append(entry)
        records[package] = entries
    return records


def main():
    import os
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('target', type=Path)
    parser.add_argument('sdk', type=Path)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    paths = [Path(path) for path in os.environ['LIBRARY_PATH'].split(':') if path]
    record = audit(args.target, args.sdk, paths)
    args.report.write_text(json.dumps(record, indent=2) + '\n')


if __name__ == '__main__':
    main()
