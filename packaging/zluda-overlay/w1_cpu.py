#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR Apache-2.0
# Adapted from the clean ZLUDA wine/w1-fixtures CPU harness; original authorship retained there.
"""Verify same-run CPU identity and PE32 stack-width fixtures with packaged Wine."""
import argparse
import json
import os
import re
import shutil
import subprocess
import time

from inputs import LOCK_FILE, HERE, LOCK, PROFILE_FILE, bridge_component, git, sha256
import wine_cpu
from pathlib import Path

BACKENDS = {'absent': '/nonexistent/w1/libcuda.so.1', 'bundled': None}


def environment(tool, work, arch, root, enabled, backend):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('WINE', 'PROTON_', 'ZLUDA_', 'W1_')) and k not in ('LD_PRELOAD', 'DISPLAY')}
    prefix = work / ('prefix-' + arch)
    for sub in ('tmp', 'runtime'):
        (work / sub).mkdir(mode=0o700, exist_ok=True)
    env.update(LD_LIBRARY_PATH=':'.join(str(tool / 'files/lib' / a)
                                        for a in ['wine/x86_64-unix', 'x86_64-linux-gnu', 'i386-linux-gnu']),
               WINEPREFIX=str(prefix), WINEARCH=arch, WINEDEBUG='-all',
               WINELOADER=str(tool / 'files/bin/wine'), WINESERVER=str(tool / 'files/bin/wineserver'),
               WINEESYNC='0', WINEFSYNC='0', WINEDLLPATH=str(tool / 'files/lib/wine-cuda' / root),
               WINEDLLOVERRIDES='nvcuda=b;winemenubuilder.exe=d',
               PROTON_ENABLE_ZLUDA='1' if enabled else '0',
               PATH=str(tool / 'files/bin') + ':' + env.get('PATH', '/usr/bin:/bin'),
               LC_ALL='C', TMPDIR=str(work / 'tmp'), XDG_RUNTIME_DIR=str(work / 'runtime'))
    if enabled and backend:
        env['ZLUDA_WINE_BACKEND_PATH'] = backend
    if os.environ.get('CI_STEAM_ROOT'):
        steam=Path(os.environ['CI_STEAM_ROOT'])
        env['LD_LIBRARY_PATH'] += ':' + ':'.join(str(steam/name/'video') for name in ('ubuntu12_64','ubuntu12_32','steamrtarm64'))
    return env


def run(name, argv, env, work, timeout, extra_env=None):
    env = dict(env, **(extra_env or {}))
    logs = work / 'logs'
    logs.mkdir(exist_ok=True)
    start = time.monotonic()
    try:
        r = subprocess.run([str(a) for a in argv], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout)
        code, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = 'timeout', e.stdout or b'', e.stderr or b''
    (logs / (name + '.jsonl')).write_bytes(out)
    (logs / (name + '.stderr')).write_bytes(err[-65536:])
    rows = []
    for line in out.decode(errors='replace').splitlines():
        if line.startswith('{'):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    cleanup_dir = logs / name
    cleanup_dir.mkdir()
    cleanup = wine_cpu.cleanup_server(Path(env['WINESERVER']), env, cleanup_dir)
    if cleanup['status'] != 'passed':
        raise ValueError('W1 fixture server cleanup failed: ' + name)
    return {'name': name, 'exit': code, 'seconds': round(time.monotonic() - start, 2), 'rows': rows, 'cleanup': cleanup}


def checks(result):
    return {r['check']: r['pass'] for r in result['rows'] if 'check' in r}


def events(result, kind):
    return [r for r in result['rows'] if r.get('event') == kind]


def terminated(result):
    """Ran to its own finish record (any verdict) instead of hanging/crashing."""
    return result['exit'] in (0, 1, 2) and bool(events(result, 'start')) and (
        bool(events(result, 'finish')) or result['exit'] == 2)


FIXTURE_PIN = LOCK['components'][bridge_component()]

def admit_fixtures(fixtures, source):
    if git(source, 'rev-parse', 'HEAD') != FIXTURE_PIN:
        raise ValueError('W1 fixture source pin mismatch')
    expected = {'source_pin': FIXTURE_PIN, 'sdk_image': LOCK['sdk_image'],
                'input_lock_sha256': sha256(LOCK_FILE),
                'base_profile_sha256': sha256(PROFILE_FILE)}
    if json.loads((fixtures / 'BUILD.json').read_text()) != expected:
        raise ValueError('W1 fixture build differs from selected source, SDK or base profile')
    seen = {}
    for inventory, base in [('SHA256SUMS', fixtures), ('SOURCE-SHA256SUMS', source)]:
        entries = {}
        for line in (fixtures / inventory).read_text().splitlines():
            digest, name = line.split(None, 1)
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()) or sha256(path) != digest or name in entries:
                raise ValueError('W1 fixture source/binary checksum mismatch: ' + name)
            entries[name] = digest
        if not entries:
            raise ValueError('empty W1 fixture inventory')
        seen[inventory] = entries
    if any(p.is_symlink() for p in fixtures.rglob('*')):
        raise ValueError('W1 fixture links are not admitted')
    actual = {p.relative_to(fixtures).as_posix() for p in fixtures.rglob('*') if p.is_file()}
    if actual != set(seen['SHA256SUMS']) | {'BUILD.json', 'SHA256SUMS', 'SOURCE-SHA256SUMS'}:
        raise ValueError('unlisted W1 fixture file')
    widths = source / 'src/pe32_widths.h'
    cases = [(name, int(correct), int(old)) for name, correct, old in
             re.findall(r'X\((\w+),\s*(\d+),\s*(\d+),', widths.read_text())]
    if len(cases) != 25 or sum(correct == old for _, correct, old in cases) != 1:
        raise ValueError('W1 fixture width list differs from reviewed 25-case scope')
    return {'source_pin': FIXTURE_PIN, 'inventories': seen, 'stack_cases': cases,
            'stack_total': len(cases), 'stack_unchanged': 1}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--fixture-source', type=Path, required=True)
    p.add_argument('--tool', type=Path, required=True)
    p.add_argument('--fixtures', type=Path, required=True)
    p.add_argument('--work', type=Path, required=True)
    a = p.parse_args()
    wine_cpu.admit_cpu_devices()
    wine_cpu.admit_tool(a.source, a.tool)
    fixture_admission = admit_fixtures(a.fixtures, a.fixture_source)
    work = a.work
    work.mkdir(parents=True)
    fx = work / 'fixtures'
    shutil.copytree(a.fixtures, fx)
    x64, pe32 = fx / 'x64', fx / 'pe32'
    results, expectations = [], []

    def expect(label, ok, detail=None):
        expectations.append({'expectation': label, 'pass': bool(ok), 'detail': detail})

    wine = a.tool / 'files/bin/wine'
    for arch in ('win64', 'wow64'):
        env = environment(a.tool, work, arch, 'stub', False, None)
        results.append(run('boot-' + arch, [wine, 'wineboot', '-u'], env, work, 600))

    # Verify the width controls before the candidate.
    wow_off = environment(a.tool, work, 'wow64', 'stub', False, None)
    st = pe32 / 'pe32_stack.exe'
    good = run('pe32-stack-stub-correct', [wine, st, '--expect', 'correct'], wow_off, work, 120,
               {'W1_NVCUDA': 'w1stub_correct.dll'})
    old = run('pe32-stack-stub-old-expect-old', [wine, st, '--expect', 'old'], wow_off, work, 120,
              {'W1_NVCUDA': 'w1stub_old.dll'})
    detect = run('pe32-stack-stub-old-expect-correct', [wine, st, '--expect', 'correct'], wow_off, work, 120,
                 {'W1_NVCUDA': 'w1stub_old.dll'})
    results += [good, old, detect]
    summary = lambda r: (events(r, 'summary') or [{}])[0]
    expect('self-test: correct stub balanced 25/25', good['exit'] == 0 and summary(good).get('matched') == 25)
    expect('self-test: old stub shows all 25 expected historical widths (including unchanged LUID)',
           old['exit'] == 0 and summary(old).get('matched') == 25)
    expect('self-test: old stub FAILS the correct-width gate on 24, with only unchanged LUID matching',
           detect['exit'] == 1 and summary(detect).get('matched') == 1)
    candidate = {}
    for backend, path in BACKENDS.items():
        env = environment(a.tool, work, 'wow64', 'zluda', True, path)
        correct = run(f'pe32-stack-candidate-{backend}-expect-correct', [wine, st, '--expect', 'correct'], env, work, 120)
        old = run(f'pe32-stack-candidate-{backend}-expect-old', [wine, st, '--expect', 'old'], env, work, 120)
        results += [correct, old]
        candidate[backend] = {'loaded': any(r.get('label') == 'nvcuda' for r in events(correct, 'module')),
                              'correct_matched': summary(correct).get('matched'),
                              'old_matched': summary(old).get('matched')}
        expect(f'candidate PE32 {backend}: correct widths 25/25',
               candidate[backend]['loaded'] and correct['exit'] == 0 and summary(correct).get('matched') == 25,
               candidate[backend])
        expect(f'candidate PE32 {backend}: 24 historical widths rejected, only unchanged LUID matching',
               old['exit'] == 1 and summary(old).get('matched') == 1, candidate[backend])

    # Verify the selected base stub and the bridge export surfaces.
    for arch, d, n in (('win64', x64, 788), ('wow64', pe32, 690)):
        off = run(f'identity-off-{arch}', [wine, d / 'identity.exe', '--expect', 'off'],
                  environment(a.tool, work, arch, 'stub', False, None), work, 120)
        on = run(f'identity-on-{arch}', [wine, d / 'identity.exe', '--expect', 'on', '--exports', str(n)],
                 environment(a.tool, work, arch, 'zluda', True, BACKENDS['absent']), work, 120)
        results += [off, on]
        expect(f'identity off {arch}: base stub, no CUDA exports', off['exit'] == 0, checks(off))
        expect(f'identity on {arch}: {n} exports, no GPU -> cuInit fails, terminates',
               checks(on).get('export-count') is True and checks(on).get('cuInit') is False and terminated(on),
               checks(on))

    record = {'tool': str(a.tool), 'fixture_admission': fixture_admission, 'candidate_stack': candidate, 'expectations': expectations,
              'results': [{k: v for k, v in r.items() if k != 'rows'} | {'checks': checks(r)} for r in results]}
    (work / 'CPU-RESULT.json').write_text(json.dumps(record, indent=2) + '\n')
    failed = [e for e in expectations if not e['pass']]
    for e in expectations:
        print(('PASS ' if e['pass'] else 'FAIL ') + e['expectation'])
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
