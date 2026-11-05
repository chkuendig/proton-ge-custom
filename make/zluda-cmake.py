#!/usr/bin/env python3
"""HiGHS's Rust bindings expect the static archive under lib/."""
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args and not args[0].startswith('-') and Path(args[0]).resolve() == Path(os.environ['ZLUDA_BUILD_SOURCE'], 'ext/HiGHS').resolve():
    args.append('-DCMAKE_INSTALL_LIBDIR=lib')
env = dict(os.environ)
env.pop('DESTDIR', None)
cmake = env['ZLUDA_REAL_CMAKE']
os.execve(cmake, [cmake, *args], env)
