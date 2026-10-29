#!/usr/bin/env python3
"""Use Proton's dependency recipes and Wine environment for the module overlay."""
import argparse
import json
from pathlib import Path
import re
import shlex

HERE = Path(__file__).resolve().parent
POLICY = json.loads((HERE / 'wine-deps.json').read_text())


def assignments(text):
    text = re.sub(r'\\\n', ' ', text)
    return dict((name, value.split()) for name, value in re.findall(
        r'^([A-Z][A-Za-z0-9_]*)\s*(?::|\+|\?)?=\s*([^\n]*)', text, re.M))


def validate(text):
    values = assignments(text)
    for arch, expected in POLICY['wine'].items():
        key = 'WINE_DEPENDS' if arch == 'common' else 'WINE_' + arch + '_DEPENDS'
        if values[key] != expected:
            raise ValueError('Wine prerequisite policy differs from Makefile.in: ' + key)
    for name, expected in POLICY['transitive'].items():
        if values[name.upper().replace('-', '_') + '_DEPENDS'] != expected:
            raise ValueError('transitive prerequisite policy differs: ' + name)
    # Every direct or transitive input must have an explicit source-path mapping.
    names = set(POLICY['extra'])
    for dependencies in [*POLICY['wine'].values(), *POLICY['transitive'].values()]:
        names.update(dependencies)
    if names != set(POLICY['sources']):
        raise ValueError('incomplete Wine prerequisite source mapping')
    for name, path in POLICY['sources'].items():
        if f'$(call rules-source,{name},$(SRCDIR)/{path})' not in text:
            raise ValueError('source path differs from Makefile.in: ' + name)


def generate(source, build):
    text = (source / 'Makefile.in').read_text()
    validate(text)
    selected = set(POLICY['sources']) | {'wine'}
    lines = []
    for line in text.splitlines():
        factory = re.match(r'\$\(eval \$\(call ([\w-]+),([^,)]+)[,)]', line)
        if factory and (factory[2] not in selected or
                        (factory[1] in ('rules-source', 'rules-wine-requests') and factory[2] == 'wine')):
            continue
        # Full-Proton NVIDIA/layer includes have their own unrelated factories.
        if line.startswith('include ') and not line.startswith('include $(SRC)/make/'):
            continue
        if line.startswith('WINE_DEPENDS ='):
            # Protocol XML is architecture independent. Build it for both Wine
            # trees as an explicit overlay prerequisite, even though this GE
            # Makefile only instantiates its aarch64 target by default.
            line += ' ' + ' '.join(POLICY['extra'])
        lines.append(line)
        if line == '$(eval $(call rules-meson,wayland-protocols,aarch64,unix))':
            for arch in ['x86_64', 'i386']:
                lines.append(f'$(eval $(call rules-meson,wayland-protocols,{arch},unix))')
    prefix = [
        '# Generated from the checked-out Makefile.in; no dependency recipes are duplicated.',
        f'SRCDIR := {source}', 'CONTAINER := 1', 'TARGET_ARCH := x86_64',
        'NO_MAKEFILE_DEPENDENCY := 1',
        # GE prepares Wine in place; this recipe prepares a scratch checkout.
        # Both source and generated-file prerequisites must use that same tree.
        f'override WINE_SRC := {build / "src/wine"}',
        f'override WINE_ORIGIN := {build / "src/wine"}',
    ]
    for arch in ['x86_64', 'i386']:
        prefix += [f'override WINE_{arch}_OBJ := {build / ("obj-" + arch)}',
                   f'override WINE_{arch}_DST := {build / ("dst-" + arch)}']
    tail = ['.PHONY: overlay-inspect', 'overlay-inspect:', '\t@:', '']
    for arch, triplet in [('x86_64', 'x86_64-linux-gnu'), ('i386', 'i386-linux-gnu')]:
        # Preserve GE's per-architecture pkg-config paths and add protocol data's
        # actual share/pkgconfig directory. All CFLAGS/LDFLAGS/configure switches
        # and dependency/post-build order otherwise come from GE's own rules.
        pc = (f'$(call list-join,:,$(foreach d,$(WINE_{arch}_DEPS),'
              f'$($(d)_{arch}_LIBDIR)/{triplet}/pkgconfig)):'
              f'$(WAYLAND_PROTOCOLS_{arch}_DST)/share/pkgconfig')
        tail += [f'WINE_{arch}_ENV += PKG_CONFIG_PATH="{pc}"',
                 f'$(OBJ)/.wine-{arch}-configure: $(MAKEFILE_LIST)',
                 f'.PHONY: overlay-wine-{arch}',
                 f'overlay-wine-{arch}: $(OBJ)/.wine-{arch}-tools '
                 f'$(foreach d,$(WINE_DEPENDS) $(WINE_{arch}_DEPENDS),$(OBJ)/.$(d)-{arch}-post-build)',
                 f'\t+cd "$(WINE_{arch}_OBJ)" && env $(WINE_{arch}_ENV) $(MAKE) $(OVERLAY_WINE_TARGETS)', '']
    build.mkdir(parents=True, exist_ok=True)
    # Source and generated request headers are already prepared by the two-tree
    # comparison. Do not rsync or copy raw request headers over that patched tree.
    # Other components retain unmodified GE source/configure/build/post-build rules.
    deps = build / 'proton-deps'
    deps.mkdir(exist_ok=True)
    (deps / '.wine-source').touch()
    (deps / '.wine-post-source').touch()
    output = build / 'proton-wine.mk'
    output.write_text('\n'.join(prefix + lines + tail) + '\n')
    return output


def write_tools(build, make, ninja, jobs):
    if jobs not in range(1, 4):
        raise ValueError('jobs must be 1..3')
    tools = build / 'proton-tools'
    tools.mkdir(parents=True, exist_ok=True)
    # The outer prerequisite graph runs one component at a time. Give that
    # component three jobs, preserving its jobserver for deeper recursive make.
    (tools / 'make').write_text(
        '#!/bin/sh\ncase " ${MAKEFLAGS:-} " in\n'
        '  *--jobserver-auth=*|*--jobserver-fds=*) exec ' + shlex.quote(str(make)) + ' "$@" ;;\n'
        '  *) exec ' + shlex.quote(str(make)) + f' -j{jobs} "$@" ;;\nesac\n')
    (tools / 'ninja').write_text('#!/bin/sh\nexec ' + shlex.quote(str(ninja)) + f' -j{jobs} "$@"\n')
    for name in ['make', 'ninja']:
        (tools / name).chmod(0o755)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('build', type=Path)
    parser.add_argument('--make-tool', type=Path)
    parser.add_argument('--ninja-tool', type=Path)
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    print(generate(args.source.resolve(), args.build.resolve()))
    if args.make_tool or args.ninja_tool:
        if not args.make_tool or not args.ninja_tool:
            parser.error('both make and ninja tool paths are required')
        write_tools(args.build.resolve(), args.make_tool, args.ninja_tool, args.jobs)


if __name__ == '__main__':
    main()
