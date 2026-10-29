#!/usr/bin/env python3
"""Read pinned Meson tool contracts and exercise tools before configuration."""
import argparse
import ast
import configparser
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

WINE_TOOLS = ['winegcc', 'winebuild', 'wrc', 'widl']
CROSS_FILES = [('zluda', 'wine/nvcuda/build-wine64.txt'),
               ('dxvk', 'build-win64.txt'), ('dxvk', 'build-win32.txt'),
               ('dxvk-nvapi', 'build-win64.txt'), ('dxvk-nvapi', 'build-win32.txt')]


def cross_file(path):
    parser = configparser.ConfigParser(interpolation=None)
    with path.open() as stream:
        parser.read_file(stream)
    return parser


def commands(source, sdk=None):
    groups = {'build': [['meson'], ['ninja'], ['cc']],
              'wow64': [['i686-w64-mingw32-gcc'], ['cc']]}
    for component, filename in CROSS_FILES:
        group = component + '/' + filename
        groups[group] = []
        for _, value in cross_file(source / component / filename).items('binaries'):
            command = ast.literal_eval(value)
            if isinstance(command, str):
                command = [command]
            if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
                raise ValueError('invalid cross-file tool: ' + value)
            if command[0] in WINE_TOOLS:
                if sdk is None:
                    continue  # Built Wine tools are checked after SDK preparation.
                command[0] = str(sdk / 'bin' / command[0])
            groups[group].append(command)
    groups['dxvk/shaders'] = [['glslang' if shutil.which('glslang') else 'glslangValidator']]
    if sdk is not None:
        groups['wine-sdk'] = [[str(sdk / 'bin' / tool)] for tool in WINE_TOOLS]
        groups['wow64'].append([str(sdk / 'bin/winebuild')])
    return groups


def preflight(source, report, sdk=None):
    report.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for component, tools in commands(source, sdk).items():
        for command in tools:
            resolved = shutil.which(command[0])
            record = {'component': component, 'command': command + ['--version'], 'resolved': resolved}
            records.append(record)
            if resolved is None:
                record['error'] = 'missing executable'
                report.write_text(json.dumps(records, indent=2) + '\n')
                raise ValueError('missing ' + component + ' tool: ' + command[0])
            try:
                result = subprocess.run([resolved, *command[1:], '--version'], text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as error:
                record['error'] = str(error)
                report.write_text(json.dumps(records, indent=2) + '\n')
                raise
            record.update(returncode=result.returncode, output=result.stdout)
            report.write_text(json.dumps(records, indent=2) + '\n')
            if result.returncode:
                raise ValueError(component + ' tool --version failed: ' + command[0] + '\n' + result.stdout)
            print(':: preflight ' + component + ': ' + command[0], flush=True)
    return records


def check_unix_headers(source, sdk, report, bridge_source=None):
    """Preprocess in CI with the relay's actual flags; never compile or link."""
    bridge = bridge_source if bridge_source is not None else source / 'zluda/wine/nvcuda'
    parser = cross_file(bridge / 'build-wine64.txt')
    flags = ast.literal_eval(parser.get('built-in options', 'c_args'))
    text = '#include "config.h"\n#include <pthread.h>\n'
    report.parent.mkdir(parents=True, exist_ok=True)
    # This Winegcc pin drops stdin inputs. A .c path also avoids depending on
    # forwarding of -x language arguments; keep the source in the build area.
    with tempfile.NamedTemporaryFile(mode='w', prefix='wine-unix-headers-', suffix='.c',
                                     dir=report.parent) as probe:
        probe.write(text)
        probe.flush()
        command = [str(sdk / 'bin/winegcc'), *flags, '-I' + str(bridge / 'include'),
                   '-pthread', '-v', '-H', '-E', '-dM', str(Path(probe.name).resolve())]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=30, cwd=bridge / 'dlls/nvcuda')
    record = {'command': command, 'source': text, 'returncode': result.returncode, 'stderr': result.stderr}
    report.with_suffix('.macros').write_text(result.stdout)
    if result.returncode:
        record['status'] = 'probe-failed'
        report.write_text(json.dumps(record, indent=2) + '\n')
        raise ValueError(f'relay header probe failed (exit {result.returncode}): {report}\n{result.stderr}')
    macros = {line.split()[1] for line in result.stdout.splitlines()
              if line.startswith('#define ') and len(line.split()) >= 2}
    required = {'HAVE_PTHREAD_H', 'PTHREAD_MUTEX_INITIALIZER', 'PTHREAD_COND_INITIALIZER'}
    forbidden = {'_WIN32', '__MSVCRT__', 'WINE_UNIX_LIB'}
    record.update(required=sorted(required), missing=sorted(required - macros),
                  forbidden=sorted(forbidden & macros))
    record['status'] = 'header-mode-failed' if record['missing'] or record['forbidden'] else 'passed'
    report.write_text(json.dumps(record, indent=2) + '\n')
    if record['status'] == 'header-mode-failed':
        raise ValueError('relay must use its own config.h and Unix pthread headers: ' + str(report))
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--sdk', type=Path)
    args = parser.parse_args()
    preflight(args.source, args.report, args.sdk)
    if args.sdk is not None:
        check_unix_headers(args.source, args.sdk, args.report.with_name('wine-unix-headers.json'))
