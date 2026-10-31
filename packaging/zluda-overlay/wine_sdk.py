#!/usr/bin/env python3
"""Expose the prepared object-tree Wine SDK without installing Wine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

import wine_provider
import wine_abi


def prepare(build, sdk, cross=None, obj=None, src=None, generated_bridge=True):
    if not generated_bridge or cross is not None:
        raise ValueError('only the generated bridge SDK is supported')
    obj = obj if obj is not None else build / 'obj-x86_64'
    src = src if src is not None else build / 'src/wine'
    include, generated = src / 'include', obj / 'include'
    tool = obj / 'tools/winebuild/winebuild'
    native = obj / 'dlls/ntdll/ntdll.so'
    required = [include / 'windef.h', include / 'wine/server_protocol.h',
                generated / 'wtypes.h', tool, native]
    for path in required:
        if not path.is_file():
            raise ValueError('prepared Wine SDK lacks: ' + str(path))
    if not os.access(tool, os.X_OK):
        raise ValueError('prepared Wine SDK tool is not executable: winebuild')
    provider = wine_provider.inspect(native)

    def link(path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.symlink_to(target)

    link(sdk / 'include', include)
    link(sdk / 'include-generated', generated)
    link(sdk / 'lib/ntdll.so', native)
    wrapper = sdk / 'bin/winebuild'
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.unlink(missing_ok=True)
    shutil.copyfile(tool, wrapper)
    wrapper.chmod(0o755)
    record = {'profile': 'generated', 'native_provider': provider,
              'cross_sha256': None, 'generated_cross': None, 'wrappers': {},
              'portable_tools': {'winebuild': hashlib.sha256(wrapper.read_bytes()).hexdigest()},
              'object': str(obj), 'source': str(src), 'include': [str(generated), str(include)],
              'cross_file': str(cross),
              'sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in required}}
    (build / 'log').mkdir(parents=True, exist_ok=True)
    (build / 'log/wine-sdk.json').write_text(json.dumps(record, indent=2) + '\n')
    wine_abi.produce(src, sdk, sdk / 'ABI-RECEIPT.json')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('build', type=Path)
    parser.add_argument('sdk', type=Path)
    parser.add_argument('--generated', action='store_true', default=True)
    parser.add_argument('--object', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    prepare(args.build.resolve(), args.sdk.resolve(), obj=args.object.resolve() if args.object else None,
            src=args.source.resolve() if args.source else None, generated_bridge=args.generated)
