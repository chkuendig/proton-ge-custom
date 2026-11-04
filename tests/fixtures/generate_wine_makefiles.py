#!/usr/bin/env python3
"""Configure-free target-name fixtures from the pinned Wine generator/inputs.

Translate only output_static_lib/output_import_lib/output_module/output_unix_lib,
PROGRAMS and the conditional /all naming rules. Object recipes/dependencies are
deliberately omitted. This is not a capture of a configured CI Makefile.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

PIN = '46b29104e3741fe23bf5e2547196a253aab88c89'
MAKEDEP_SHA256 = '3853f476d02990bf51441b17675e79208ae0b14131e2383d91f7fd4e3143ee78'
COMPONENTS = ['include', 'tools/winebuild', 'tools/winegcc', 'tools/widl', 'tools/wrc',
              'tools/wmc', 'dlls/winecrt0', 'dlls/kernel32', 'dlls/ntdll', 'server', 'programs/wineboot']


def generate(repo, output):
    hashes = {}
    def source(path):
        data = subprocess.check_output(['git', '-C', str(repo), 'show', PIN + ':' + path])
        hashes[path] = hashlib.sha256(data).hexdigest()
        return data.decode()
    makedep = source('tools/makedep.c')
    if hashes['tools/makedep.c'] != MAKEDEP_SHA256:
        raise ValueError('re-audit fixture translation against changed tools/makedep.c')
    # Pin the naming formats and the specific distinction which caused run 4.
    assert 'arch_dirs[0] = "";' in makedep
    assert 'arch_dirs[arch] = strmake( "%s-windows/", archs.str[arch] );' in makedep
    static = makedep.split('static void output_static_lib(', 1)[1].split('static void ', 1)[0]
    assert 'strmake( "%s%s", arch_dirs[arch], make->staticlib )' in static
    assert 'all_targets' not in static
    assert 'strmake( "%slib%s.a", arch_dirs[arch], make->importlib )' in makedep
    inputs = {name: source(name + '/Makefile.in') for name in COMPONENTS}
    output.mkdir(parents=True, exist_ok=True)
    for arch in ['x86_64', 'i386']:
        rules, aggregates = [], []
        for name, text in inputs.items():
            values = dict(re.findall(r'^(MODULE|UNIXLIB|STATICLIB|IMPORTLIB|PROGRAMS)\s*=\s*(\S+)', text, re.M))
            all_targets = []
            if 'STATICLIB' in values:
                rules.append(f'{name}/{arch}-windows/{values["STATICLIB"]}:')
                rules.append(f'{name}/{values["STATICLIB"]}:')
            if 'IMPORTLIB' in values:
                rules.append(f'{name}/{arch}-windows/lib{values["IMPORTLIB"]}.a:')
                rules.append(f'{name}/lib{values["IMPORTLIB"]}.a:')
            if 'MODULE' in values:
                all_targets.append(f'{name}/{arch}-windows/{values["MODULE"]}')
            if 'UNIXLIB' in values:
                all_targets.append(name + '/' + values['UNIXLIB'])
            if 'PROGRAMS' in values:
                all_targets.append(name + '/' + values['PROGRAMS'])
            if name == 'include':
                # IDL generates headers, making include/all nonempty.
                idl = re.search(r'\b([\w]+)\.idl\b', text)[1]
                all_targets.append('include/' + idl + '.h')
            rules.extend(target + ':' for target in all_targets)
            if all_targets:
                rules.append(name + '/all: ' + ' '.join(all_targets))
                aggregates.append(name + '/all')
        (output / ('wine-' + arch + '.mk')).write_text(
            '# Configure-free target fixture; see generate_wine_makefiles.py\n'
            '# Wine pin: ' + PIN + '\nall: ' + ' '.join(aggregates) + '\n' + '\n'.join(rules) + '\n')
    (output / 'wine-targets-source.json').write_text(json.dumps(
        {'wine': PIN, 'method': 'configure-free target-name translation; recipes omitted',
         'sha256': hashes}, indent=2) + '\n')


if __name__ == '__main__':
    generate(Path(sys.argv[1]), Path(__file__).resolve().parent)
