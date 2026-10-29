#!/usr/bin/env python3
"""Select overlay/SDK build goals from Wine's configured top-level Makefile."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re


def declared_targets(text):
    targets = set()
    for line in re.sub(r'\\\n', ' ', text).splitlines():
        if not line or line.startswith(('\t', '#')) or ':' not in line:
            continue
        lhs, rhs = line.split(':', 1)
        if '=' in lhs or rhs.startswith('='):
            continue
        for target in lhs.split():
            if not target.startswith('.') and not any(char in target for char in '$%#'):
                targets.add(target)
    return targets


def select(text, arch, profile, modules):
    targets = declared_targets(text)
    selected = []

    def require(target):
        if target not in targets:
            raise ValueError('Wine Makefile has no declared target: ' + target)
        if target not in selected:
            selected.append(target)

    def component(name):
        if not re.fullmatch(r'(?:dlls|programs|tools)/[\w.+-]+|include|server', name):
            raise ValueError('invalid Wine component: ' + name)
        if name + '/all' in targets:
            require(name + '/all')
            return
        # Static libraries need not have an /all rule. Use the actual archive
        # producers for this PE architecture, never invent the aggregate name.
        libraries = [target for target in targets if target.startswith(name + '/')
                     and target.endswith('.a') and not target.endswith('.delay.a')
                     and '/' + arch + '-windows/' in target]
        if not libraries:
            raise ValueError('Wine component has no aggregate/archive targets: ' + name)
        for target in sorted(libraries):
            require(target)

    def sdk_library(name):
        candidates = [target for target in targets if target.startswith(name + '/')
                      and target.endswith('.a') and not target.endswith('.delay.a')
                      and '/' + arch + '-windows/' in target]
        if len(candidates) != 1:
            raise ValueError('expected one Wine SDK archive target: ' + name)
        require(candidates[0])
        # Native Winegcc builds .dll.so relays using ELF archives in the DLL
        # directory itself; PE archives alone cannot satisfy that link.
        native = name + '/lib' + name.split('/')[-1] + '.a'
        require(native)

    if profile not in ('all', 'both', 'x86_64-unix-ntdll'):
        raise ValueError('unknown Wine build profile: ' + profile)
    if profile == 'x86_64-unix-ntdll' and arch != 'x86_64':
        raise ValueError('native-only Wine profile cannot build i386')
    if profile == 'all':
        require('all')
    else:
        component('include')
    if arch == 'x86_64':
        for tool in ['winebuild', 'winegcc', 'widl', 'wrc', 'wmc']:
            component('tools/' + tool)
        # The external winegcc relay needs these import/static libraries even
        # when only the Unix ntdll is overlaid. /all does not imply import libs.
        for library in ['winecrt0', 'kernel32', 'ntdll']:
            sdk_library('dlls/' + library)
    if profile == 'x86_64-unix-ntdll':
        require('dlls/ntdll/ntdll.so')
    elif profile == 'both':
        if not modules:
            raise ValueError('module-local Wine build has no modules')
        for module in modules:
            component(module)
    return selected


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('makefile', type=Path)
    parser.add_argument('--arch', choices=['x86_64', 'i386'], required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--modules', type=Path, required=True)
    parser.add_argument('--make-tool', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    text = args.makefile.read_text()
    # Preserve the real configured rules even if goal discovery fails. This
    # makes future naming regressions inspectable without another Wine configure.
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.with_suffix('.Makefile.gz').write_bytes(gzip.compress(text.encode(), mtime=0))
    modules = [line for line in args.modules.read_text().splitlines() if line]
    targets = select(text, args.arch, args.profile, modules)
    record = {'arch': args.arch, 'profile': args.profile, 'modules': modules,
              'makefile_sha256': hashlib.sha256(text.encode()).hexdigest(), 'targets': targets}
    args.report.write_text(json.dumps(record, indent=2) + '\n')
    print(':: Wine targets: ' + ' '.join(targets), flush=True)
    os.execv(args.make_tool, [args.make_tool, *targets])


if __name__ == '__main__':
    main()
