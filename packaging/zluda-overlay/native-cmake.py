#!/usr/bin/env python3
"""Keep HiGHS's install directory consistent with its pinned Rust bindings."""
import os
from pathlib import Path
import sys


def main():
    args = sys.argv[1:]
    source = Path(os.environ['SOURCE_ROOT']) / 'zluda/ext/HiGHS'
    # cmake-rs 0.1.54 passes the source as its first configure argument.
    # Do not rewrite LLVM, --build, --version or any GE/Wine invocation.
    if args and not args[0].startswith('-') and Path(args[0]).resolve() == source.resolve():
        args.append('-DCMAKE_INSTALL_LIBDIR=lib')
    env = dict(os.environ)
    env.pop('DESTDIR', None)
    cmake = env['ZLUDA_REAL_CMAKE']
    os.execve(cmake, [cmake, *args], env)


if __name__ == '__main__':
    main()
