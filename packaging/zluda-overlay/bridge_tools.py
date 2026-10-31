#!/usr/bin/env python3
"""Read pinned Meson tool contracts and exercise tools before configuration."""
import argparse
import ast
import configparser
import json
from pathlib import Path
import shutil
import subprocess

CROSS_FILES = [('dxvk', 'build-win64.txt'), ('dxvk', 'build-win32.txt'),
               ('dxvk-nvapi', 'build-win64.txt'), ('dxvk-nvapi', 'build-win32.txt')]


def cross_file(path):
    parser = configparser.ConfigParser(interpolation=None)
    with path.open() as stream:
        parser.read_file(stream)
    return parser


def commands(source, sdk=None, generated_bridge=True):
    groups = {'build': [['meson'], ['ninja'], ['cc']],
              'wow64': [['i686-w64-mingw32-gcc'], ['cc']]}
    if generated_bridge:
        groups['generated-bridge'] = [[tool] for tool in ['rustc', 'cargo',
             'x86_64-w64-mingw32-gcc', 'i686-w64-mingw32-gcc',
             'x86_64-w64-mingw32-dlltool', 'i686-w64-mingw32-dlltool',
             'x86_64-w64-mingw32-objcopy', 'i686-w64-mingw32-objcopy']]
    for component, filename in CROSS_FILES:
        group = component + '/' + filename
        groups[group] = []
        for _, value in cross_file(source / component / filename).items('binaries'):
            command = ast.literal_eval(value)
            if isinstance(command, str):
                command = [command]
            if not isinstance(command, list) or not command or not all(isinstance(arg, str) for arg in command):
                raise ValueError('invalid cross-file tool: ' + value)
            groups[group].append(command)
    groups['dxvk/shaders'] = [['glslang' if shutil.which('glslang') else 'glslangValidator']]
    if sdk is not None:
        groups['wine-sdk'] = [[str(sdk / 'bin' / tool)] for tool in ['winebuild']]
        groups['wow64'].append([str(sdk / 'bin/winebuild')])
    return groups


def preflight(source, report, sdk=None, generated_bridge=True):
    report.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for component, tools in commands(source, sdk, generated_bridge).items():
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--generated', action='store_true', default=True)
    args = parser.parse_args()
    preflight(args.source, args.report, args.sdk, args.generated)
