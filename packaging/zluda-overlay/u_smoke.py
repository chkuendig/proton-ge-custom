#!/usr/bin/env python3
"""CPU-only smoke test of an assembled route-U tool through its own `proton`.

Runs ZLUDA's PE32 route probe the way a 32-bit PhysX title starts
(PROTON_USE_WOW64=1, PROTON_ENABLE_ZLUDA=1, outside Steam via UMU_ID):

* packaged: the tool as built. Without a GPU, cuInit must fail quickly, not
  hang. The launch glue must have installed zluda32 and the server, the
  server must have loaded the tool's own libcuda and HIP, and a second
  launch must repair a tampered prefix.
* stub: a copy of the tool whose libcuda.so.1 is the route stub driver. The
  probe's context, sampler and version checks must pass end to end.

This is not GPU or Deck qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


def sha256(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def server_processes():
    out = subprocess.run(['pgrep', '-af', 'zluda64_server'], capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if 'pgrep' not in line]


def launch(tool, work, name, probe, iterations, extra_env=None, timeout=420, zluda=True):
    compat = work / 'compat'
    steam = work.parent / 'steam'
    compat.mkdir(parents=True, exist_ok=True)
    steam.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WINE', 'PROTON_', 'LD_'))}
    env.update(STEAM_COMPAT_DATA_PATH=str(compat), STEAM_COMPAT_CLIENT_INSTALL_PATH=str(steam),
               UMU_ID='0', PROTON_USE_WOW64='1', PROTON_LOG='1', PROTON_LOG_DIR=str(work),
               SteamGameId=name, WINEDEBUG='err+all,warn+module,+loaddll,fixme+nvcuda,-fixme+all')
    if zluda:
        env['PROTON_ENABLE_ZLUDA'] = '1'
    env.update(extra_env or {})
    server_env = dict(env, WINEPREFIX=str(compat / 'pfx'))
    start = time.monotonic()
    # A unix path makes GE start the exe through umu.exe with a new Wine
    # console; headless, the probe then blocks on its first console write.
    # A Z: path takes GE's plain `wine <exe>` branch, which inherits stdout.
    windows_probe = 'Z:' + str(probe).replace('/', '\\')
    proc = subprocess.Popen(['python3', str(tool / 'proton'), 'run', windows_probe, str(iterations)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        code = 'timeout'
        # Record what is still running before tearing the prefix down.
        (work / (name + '.ps')).write_text(subprocess.run(
            ['ps', '-eo', 'pid,ppid,stat,etime,args', '--forest'], capture_output=True, text=True).stdout)
        subprocess.run([str(tool / 'files/bin/wineserver'), '-k'], env=server_env, timeout=60)
        proc.kill()
        out, err = proc.communicate()
    seconds = round(time.monotonic() - start, 2)
    (work / (name + '.stdout')).write_text(out)
    (work / (name + '.stderr')).write_text(err[-200000:])
    try:
        subprocess.run([str(tool / 'files/bin/wineserver'), '-w'], env=server_env, timeout=60)
    except subprocess.TimeoutExpired:
        (work / (name + '.wineserver-timeout')).write_text('wineserver -w exceeded 60 s; killed\n')
        subprocess.run([str(tool / 'files/bin/wineserver'), '-k'], env=server_env, timeout=60)
    # With PROTON_LOG the game's stdout goes to steam-<SteamGameId>.log.
    log = work / ('steam-' + name + '.log')
    text = out + (log.read_text(errors='replace') if log.is_file() else '')
    loader = [l for l in text.splitlines() if 'nvcuda' in l.lower() or ':module:' in l]
    print('   loader:', *loader[:30], sep='\n     ', flush=True)
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    print(f'== {name}: exit {code}, {seconds} s, {len(rows)} probe rows', flush=True)
    for row in rows[:40]:
        print('  ', json.dumps(row), flush=True)
    if not rows:
        print('   stdout tail:', out[-1500:], '\n   stderr tail:', err[-3000:], flush=True)
    return {'exit': code, 'seconds': seconds, 'rows': rows, 'loader': loader, 'stub_tex': [l for l in err.splitlines() if 'STUB: TEX' in l],
            'servers_after': server_processes()}


def wineboot_link_hazard(tool, work, syswow):
    builtins = tool / 'files/lib/wine/i386-windows'
    control = next(n for n in ['xinput1_3.dll', 'xinput9_1_0.dll', 'msacm32.dll']
                   if (builtins / n).is_file() and (syswow / n).exists())
    donor = (builtins / 'version.dll').read_bytes()
    assert donor != (builtins / control).read_bytes()
    other = work / 'other-tool/files/lib/wine/i386-windows'
    other.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WINE', 'PROTON_', 'LD_'))}
    env.update(WINEPREFIX=str(syswow.parents[2]), WINEDEBUG='-all')
    checks = {}
    for kind in ['symlink', 'hardlink']:
        for name in [control, 'nvcuda.dll']:
            (other / name).write_bytes(donor)
            if os.path.lexists(syswow / name):
                (syswow / name).unlink()
            if kind == 'symlink':
                (syswow / name).symlink_to(other / name)
            else:
                os.link(other / name, syswow / name)
        r = subprocess.run([str(tool / 'files/bin/wine'), 'wineboot', '-u'], env=env,
                           capture_output=True, text=True, timeout=600)
        subprocess.run([str(tool / 'files/bin/wineserver'), '-w'], env=env, timeout=120)
        (work / ('wineboot-' + kind + '.stderr')).write_text(r.stderr[-50000:])
        written = (other / control).read_bytes() == (builtins / control).read_bytes()
        print(f'   wineboot -u ({kind}): exit {r.returncode}, control {control} written through: {written}', flush=True)
        checks[f'wineboot_{kind}_control_written_through'] = written
        checks[f'wineboot_{kind}_other_tool_nvcuda_unchanged'] = (other / 'nvcuda.dll').read_bytes() == donor
    return checks


def row(rows, **match):
    return next((r for r in rows if all(r.get(k) == v for k, v in match.items())), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--stub', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    tool, work = args.tool.resolve(), args.work.resolve()
    work.mkdir(parents=True)
    client = tool / 'files/lib/wine/zluda/i386-windows/nvcuda.dll'
    server = tool / 'files/lib/wine/zluda/zluda64_server.exe'
    checks, results = {}, {}
    # Route U ships no i386 nvcuda builtin, so Wine has nothing to install
    # (or to write through prefix links) under that name.
    checks['tool_has_no_i386_nvcuda_builtin'] = not os.path.lexists(tool / 'files/lib/wine/i386-windows/nvcuda.dll')
    checks['default_pfx_has_no_syswow64_nvcuda'] = not os.path.lexists(
        tool / 'files/share/default_pfx/drive_c/windows/syswow64/nvcuda.dll')

    # Launcher baseline: same tool and probe without ZLUDA (GE's own nvcuda
    # handling). If this cannot run, the harness rather than U is at fault.
    r = results['launcher'] = launch(tool, work / 'launcher', 'launcher', args.probe, 10, zluda=False)
    checks['launcher_runs_probe'] = r['exit'] != 'timeout' and row(r['rows'], probe='start') is not None
    # Both files are installed together; the server is the unambiguous witness
    # (GE itself copies U's builtin nvcuda.dll into syswow64).
    checks['zluda_off_installs_nothing'] = not (work / 'launcher/compat/pfx/drive_c/windows/zluda64_server.exe').exists()

    # Steam runs d3ddriverquery64.exe under every tool; even with ZLUDA on
    # in the environment, a helper launch must not touch the prefix.
    helper = work / 'helper/d3ddriverquery64.exe'
    helper.parent.mkdir(parents=True)
    shutil.copyfile(args.probe, helper)
    r = results['helper'] = launch(tool, work / 'helper', 'helper', helper, 10)
    checks['helper_runs'] = r['exit'] != 'timeout'
    checks['helper_installs_nothing'] = not (work / 'helper/compat/pfx/drive_c/windows/zluda64_server.exe').exists()

    # Packaged tool, real ZLUDA, no GPU.
    packaged = work / 'packaged'
    trace = packaged / 'ld'
    trace.mkdir(parents=True)
    r = results['packaged-1'] = launch(tool, packaged, 'packaged-1', args.probe, 200,
                                       {'LD_DEBUG': 'files', 'LD_DEBUG_OUTPUT': str(trace / 'ld')})
    windows = packaged / 'compat/pfx/drive_c/windows'
    init = row(r['rows'], step='cuInit')
    checks['packaged_probe_loaded_nvcuda'] = row(r['rows'], step='LoadLibrary', result=0) is not None
    checks['packaged_cuInit_fails_without_gpu'] = init is not None and init['result'] != 0
    checks['packaged_no_hang'] = r['exit'] != 'timeout' and init is not None and init['us'] < 20e6
    checks['glue_installed_client'] = (windows / 'syswow64/nvcuda.dll').is_file() and \
        sha256(windows / 'syswow64/nvcuda.dll') == sha256(client)
    checks['glue_installed_server'] = (windows / 'zluda64_server.exe').is_file() and \
        sha256(windows / 'zluda64_server.exe') == sha256(server)
    # ld.so writes one file per process; the server's maps the tool's libraries.
    loaded = {}
    for path in trace.iterdir():
        text = path.read_text(errors='replace')
        for name in ['libcuda.so.1', 'libamdhip64.so.7']:
            for line in text.splitlines():
                if 'calling init:' in line and line.rstrip().endswith('/' + name):
                    loaded.setdefault(name, set()).add(line.split('calling init:', 1)[1].strip())
    results['packaged-1']['loaded'] = {k: sorted(v) for k, v in loaded.items()}
    print('   loaded:', results['packaged-1']['loaded'], flush=True)
    for name in ['libcuda.so.1', 'libamdhip64.so.7']:
        paths = [Path(p) for p in loaded.get(name, [])]
        checks['server_loaded_tool_' + name] = bool(paths) and all(
            p.resolve().is_relative_to(tool / 'files/lib64') for p in paths)
    checks['packaged_no_stray_server'] = not r['servers_after']
    # A 32-bit WoW64 process sees syswow64 as system32, so Wine logs e.g.
    # 'Loaded L"C:\\windows\\system32\\nvcuda.dll" at 7B7C0000: native'.
    # Tell the game's load from the 64-bit server's by the 8-digit address.
    checks['packaged_client_loaded_native'] = any(
        'loaddll' in l and 'system32' in l and 'nvcuda.dll' in l and re.search(r' at [0-9A-F]{8}: native$', l.rstrip())
        for l in r['loader'])
    # GE resets syswow64/nvcuda.dll each launch; the glue must restore both files.
    if not (windows / 'syswow64').is_dir():
        for key in ['relaunch_restores_client', 'relaunch_restores_server', 'relaunch_same_cuInit',
                    'relaunch_no_stray_server']:
            checks[key] = False
    else:
        (windows / 'syswow64/nvcuda.dll').write_bytes(b'tampered')
        (windows / 'zluda64_server.exe').unlink(missing_ok=True)
        r = results['packaged-2'] = launch(tool, packaged, 'packaged-2', args.probe, 200)
        init2 = row(r['rows'], step='cuInit')
        checks['relaunch_restores_client'] = sha256(windows / 'syswow64/nvcuda.dll') == sha256(client)
        checks['relaunch_restores_server'] = (windows / 'zluda64_server.exe').is_file() and \
            sha256(windows / 'zluda64_server.exe') == sha256(server)
        checks['relaunch_same_cuInit'] = init2 is not None and init is not None and init2['result'] == init['result']
        checks['relaunch_no_stray_server'] = not r['servers_after']

    # Wine's fake-dll install truncates an existing builtin prefix DLL in
    # place, following links. Point syswow64 entries at an "other tool" tree
    # and run U's prefix update. The control builtin must be written through
    # (the hazard is real here); nvcuda must not, since U has no such builtin.
    if (windows / 'syswow64').is_dir():
        checks.update(wineboot_link_hazard(tool, packaged, windows / 'syswow64'))

    # Same tool with the stub driver in place of ZLUDA's libcuda.
    stub_tool = work / 'tool-stub'
    shutil.copytree(tool, stub_tool, symlinks=True)
    (stub_tool / 'files/lib64/libcuda.so.1').unlink()
    shutil.copyfile(args.stub, stub_tool / 'files/lib64/libcuda.so.1')
    r = results['stub'] = launch(stub_tool, work / 'stub', 'stub', args.probe, 2000)
    rows = r['rows']
    checks['stub_cuInit_ok'] = row(rows, step='cuInit', result=0) is not None
    checks['stub_luid_801'] = any(isinstance(x.get('luid'), dict) and x['luid'].get('result') == 801
                                  for x in rows)
    checks['stub_pop_created'] = row(rows, check='pop_created', result=0, same=1) is not None
    for name in ['pop_empty', 'push_null', 'push_unknown']:
        checks['stub_' + name + '_invalid_context'] = row(rows, check=name, result=201) is not None
    checks['stub_push_current'] = row(rows, check='push_current', result=0, same=1) is not None
    checks['stub_texref_setters'] = row(rows, check='texref_setters', gettexref=0, any_error=0) is not None
    checks['stub_api_version'] = row(rows, check='api_version', value=3020) is not None
    checks['stub_probe_finished'] = row(rows, probe='finish') is not None
    checks['stub_no_stray_server'] = not r['servers_after']
    results['stub']['stub_tex_lines'] = len(r['stub_tex'])

    (work / 'RESULT.json').write_text(json.dumps(results, indent=2, default=str) + '\n')
    (work / 'CHECKS.json').write_text(json.dumps(checks, indent=2) + '\n')
    for key, value in checks.items():
        print(('PASS ' if value else 'FAIL ') + key, flush=True)
    raise SystemExit(0 if all(checks.values()) else 1)


if __name__ == '__main__':
    main()
