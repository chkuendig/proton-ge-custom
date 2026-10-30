#!/usr/bin/env python3
"""Run pinned bridge fixtures with strict GE canonical-stub admission."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import cuda_package
from inputs import sha256


def require_ge_stubs(dll_dir):
    dll_dir = Path(dll_dir)
    allowed = {arch + '/nvcuda.dll': digest
               for arch, digest in cuda_package.policy.STUB_SHA256.items()}
    for arch in ['x86_64-windows', 'i386-windows', 'x86_64-unix', 'i386-unix']:
        for name in cuda_package.CANONICAL_NAMES:
            relative = arch + '/' + name
            path = dll_dir / relative
            if relative in allowed:
                if path.is_symlink() or not path.is_file() or sha256(path) != allowed[relative]:
                    raise ValueError('canonical GE stub mismatch: ' + str(path))
            elif path.exists() or path.is_symlink():
                raise ValueError('unexpected canonical CUDA module: ' + str(path))
    return allowed


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--runner', type=Path, required=True)
    parser.add_argument('--admission', type=Path, required=True)
    args, forwarded = parser.parse_known_args()
    dll_dir = Path(forwarded[forwarded.index('--dll-dir') + 1])
    allowed = require_ge_stubs(dll_dir)
    spec = importlib.util.spec_from_file_location('pinned_bridge_fixture', args.runner)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    common = runner.common if args.runner.name == 'run-lifecycle.py' else runner
    common.require_no_canonical_module = require_ge_stubs
    args.admission.write_text(json.dumps({
        'adapter_sha256': sha256(Path(__file__)), 'runner_sha256': sha256(args.runner),
        'canonical_GE_stubs': allowed,
        'scope': 'Only canonical namespace admission is adapted; pinned cases remain unchanged'
    }, indent=2) + '\n')
    sys.argv = [str(args.runner), *forwarded]
    runner.main()


if __name__ == '__main__':
    main()
