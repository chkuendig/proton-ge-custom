#!/usr/bin/env python3
"""Real GE A1/B5 -> Valve -> GE prefix transitions, with private tool copies."""
import argparse
import json
import os
from pathlib import Path
import shutil
import stat

from inputs import sha256
import launcher_cpu
import wine_cpu


def snapshot(tool):
    records = {}
    for path in sorted(tool.rglob('*')):
        relative = path.relative_to(tool)
        if '__pycache__' in relative.parts or relative.as_posix() == 'dist.lock':
            continue
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            records[relative.as_posix()] = {'link': os.readlink(path)}
        elif stat.S_ISREG(info.st_mode):
            records[relative.as_posix()] = {'sha256': sha256(path), 'mtime_ns': info.st_mtime_ns}
    return records


def census(prefix, roots, require_private):
    windows = prefix / 'drive_c/windows'
    aliases = []
    for directory, dirs, files in os.walk(windows, followlinks=False):
        for name in dirs:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError('linked Windows parent in cross-tool prefix: ' + str(path))
        for name in files:
            path = Path(directory) / name
            if path.is_symlink():
                target = path.resolve()
                if any(target.is_relative_to(root.resolve()) for root in roots):
                    aliases.append(path.relative_to(prefix).as_posix())
            elif path.stat().st_nlink > 1:
                aliases.append(path.relative_to(prefix).as_posix())
    if require_private and aliases:
        raise ValueError('cross-tool prefix retained builtin aliases: ' + repr(aliases[:10]))
    return aliases


def run(tool, b5_tool, a1_tool, work):
    wine_cpu.admit_cpu_devices()
    work.mkdir(parents=True, exist_ok=False)
    if a1_tool is not None and not (a1_tool / 'proton').is_file():
        a1_tool = None
    report = {'status': 'running', 'scope': 'private CPU prefixes and tools; no installed tool or games',
              'sequences': [], 'tool_changes': {}}
    tools = {}
    for name, original in [('Valve', tool), ('B5', b5_tool), ('A1', a1_tool)]:
        if original is None:
            continue
        destination = work / ('tool-' + name)
        wine_cpu.command(['cp', '--reflink=auto', '-a', original, destination], dict(os.environ),
                         work / ('copy-' + name + '.log'), 600)
        tools[name] = destination
    before = {name: snapshot(path) for name, path in tools.items()}
    try:
        for baseline in ['B5', 'A1']:
            if baseline not in tools:
                continue
            for architecture in ['win64', 'wow64']:
                directory = work / (baseline + '-' + architecture)
                for name in ['tmp', 'runtime', 'home', 'game', 'steam', 'compatdata']:
                    (directory / name).mkdir(parents=True, mode=0o700)
                prefix = directory / 'compatdata/pfx'
                sequence = {'baseline': baseline, 'architecture': architecture, 'launches': []}
                report['sequences'].append(sequence)
                for step, name in enumerate([baseline, 'Valve', baseline]):
                    selected = tools[name]
                    label = str(step) + '-' + name
                    phase = directory / label
                    phase.mkdir()
                    env = launcher_cpu.environment(selected, directory, architecture, label)
                    env.update(PROTON_ENABLE_ZLUDA='0', PROTONFIXES_DISABLE='1',
                               PROTON_USE_WOW64='1' if architecture == 'wow64' else '0',
                               SteamAppId='999999', SteamGameId='999999', UMU_ID='999999',
                               STEAM_COMPAT_APP_ID='999999')
                    cleanup_env = wine_cpu.environment(selected, directory, selected / 'files/bin/wine',
                                                       selected / 'files/bin/wineserver', architecture)
                    cleanup_env['WINEPREFIX'] = str(prefix)
                    result = {'tool': name, 'status': 'running'}
                    sequence['launches'].append(result)
                    try:
                        wine_cpu.command([selected / 'proton', 'run', r'C:\windows\system32\cmd.exe',
                                          '/c', 'exit', '0'], env, phase / 'launcher.log', 240)
                    finally:
                        result['cleanup'] = wine_cpu.cleanup_server(selected / 'files/bin/wineserver',
                                                                   cleanup_env, phase / 'cleanup')
                        if result['cleanup']['status'] != 'passed':
                            raise ValueError('cross-tool server cleanup failed')
                    if not (prefix / 'system.reg').is_file():
                        raise ValueError('cross-tool launch did not create a real prefix')
                    result['prefix_aliases'] = census(prefix, tools.values(), require_private=name != 'A1')
                    for source_name, source_tool in tools.items():
                        after = snapshot(source_tool)
                        differences = [path for path in set(before[source_name]) | set(after)
                                       if before[source_name].get(path) != after.get(path)]
                        if differences:
                            report['tool_changes'][source_name] = sorted(differences)
                            raise ValueError('cross-tool launch mutated tool payload: ' + source_name)
                    result['status'] = 'passed'
        report['status'] = 'passed'
    finally:
        (work / 'RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for name in ['tool', 'b5-tool', 'work']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--a1-tool', type=Path)
    args = parser.parse_args()
    run(args.tool, args.b5_tool, args.a1_tool, args.work)
