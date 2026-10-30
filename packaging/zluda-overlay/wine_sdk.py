#!/usr/bin/env python3
"""Expose the prepared object-tree Wine SDK without installing Wine."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shlex

from bridge_tools import cross_file
import wine_provider

TOOLS = ['winegcc', 'winebuild', 'wrc', 'widl']
LIBRARIES = ['winecrt0', 'kernel32', 'ntdll']


def prepare(build, sdk, cross=None, obj=None, src=None, generated_bridge=False):
    obj = obj if obj is not None else build / 'obj-x86_64'
    src = src if src is not None else build / 'src/wine'
    include, generated = src / 'include', obj / 'include'
    required = [include / 'windef.h', include / 'wine/server_protocol.h', generated / 'wtypes.h']
    tools = ['winebuild'] if generated_bridge else TOOLS
    required += [obj / 'tools' / name / name for name in tools]
    libraries = {}
    for arch in ([] if generated_bridge else ['x86_64-unix', 'x86_64-windows']):
        for name in LIBRARIES:
            relative = Path('dlls') / name
            if arch.endswith('-windows'):
                relative /= arch
            libraries[(arch, name)] = obj / relative / ('lib' + name + '.a')
    required += list(libraries.values())
    native = obj / 'dlls/ntdll/ntdll.so' if generated_bridge else None
    if native is not None:
        required.append(native)
    for path in required:
        if not path.is_file():
            raise ValueError('prepared Wine SDK lacks: ' + str(path))
    for name in tools:
        if not os.access(obj / 'tools' / name / name, os.X_OK):
            raise ValueError('prepared Wine SDK tool is not executable: ' + name)
    provider = wine_provider.inspect(native) if native is not None else None
    (sdk / 'bin').mkdir(parents=True, exist_ok=True)

    def link(path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.symlink_to(target)

    link(sdk / 'include', include)
    link(sdk / 'include-generated', generated)
    if native is not None:
        link(sdk / 'lib/ntdll.so', native)
    for (arch, name), path in libraries.items():
        link(sdk / 'lib/wine' / arch / ('lib' + name + '.a'), path)
    for name in tools:
        command = [str(obj / 'tools' / name / name)]
        headers = []
        if name == 'winegcc':
            # Never inject -l flags here: winegcc treats them as file inputs,
            # converting --version/-E probes into link attempts. Meson owns links.
            # Match installed winegcc: project -I paths precede Wine headers.
            # winegcc itself appends obj/include after the caller's arguments.
            # Prepending it would shadow nvcuda/include/config.h with Wine's.
            command += ['--wine-objdir=' + str(obj), '-fshort-wchar', '-DWINE_UNICODE_NATIVE']
            headers = ['-isystem', str(include)]
        elif name in ['wrc', 'widl']:
            headers = ['-I' + str(generated), '-I' + str(include)]
        wrapper = sdk / 'bin' / name
        wrapper.unlink(missing_ok=True)
        version_alias = ('case "${1:-}" in --version) shift; set -- -V "$@" ;; esac\n'
                         if name == 'widl' else '')
        wrapper.write_text('#!/bin/sh\n' + version_alias + 'exec ' + shlex.join(command) +
                           ' "$@" ' + shlex.join(headers) + '\n')
        wrapper.chmod(0o755)
    if not generated_bridge:
        original = cross_file(cross)
        link_args = ast.literal_eval(original.get('built-in options', 'c_link_args'))
        if not isinstance(link_args, list) or not all(isinstance(arg, str) for arg in link_args):
            raise ValueError('invalid nvcuda c_link_args')
        # Meson's later cross file overrides only these keys, retaining pinned ABI,
        # machine and compiler options. Libraries apply to link/sanity steps only.
        (sdk / 'nvcuda-wine64.ini').write_text(
            '[binaries]\nc = ' + repr(str(sdk / 'bin/winegcc')) + '\n\n[built-in options]\n'
            'c_link_args = ' + repr(link_args + ['-l' + name for name in LIBRARIES]) + '\n')
    record = {'profile': 'generated' if generated_bridge else 'winelib',
              'native_provider': provider,
              'cross_sha256': hashlib.sha256(cross.read_bytes()).hexdigest() if cross else None,
              'generated_cross': (sdk / 'nvcuda-wine64.ini').read_text() if not generated_bridge else None,
              'wrappers': {name: (sdk / 'bin' / name).read_text() for name in tools},
              'object': str(obj), 'source': str(src), 'include': [str(generated), str(include)],
              'cross_file': str(cross), 'sha256': {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                                for path in required}}
    (build / 'log').mkdir(parents=True, exist_ok=True)
    (build / 'log/wine-sdk.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('build', type=Path)
    parser.add_argument('sdk', type=Path)
    parser.add_argument('cross', type=Path, nargs='?')
    parser.add_argument('--generated', action='store_true')
    parser.add_argument('--object', type=Path)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    prepare(args.build.resolve(), args.sdk.resolve(), args.cross.resolve() if args.cross else None,
            args.object.resolve() if args.object else None,
            args.source.resolve() if args.source else None, args.generated)
