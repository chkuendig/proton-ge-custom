#!/usr/bin/env python3
"""Admit the separate real-Wine callback/loader/thread lifecycle fixture."""
from collections import Counter
import importlib.util
import json
from pathlib import Path

import cuda_package
from inputs import LOCK, PROFILE_FILE, base_artifact, sha256
import wine_cpu

MODES = ['free-library', 'exit-process', 'thread-exit']
QUALIFICATION = 'real-Wine-synchronous-context-destructors-and-TLS-thread-detach'




def check_result(results, expected_inputs, expected_events, expected_pe_events, on_root, backend):
    record = json.loads((results / 'RESULT.json').read_text())
    if (record.get('qualification') != QUALIFICATION or record.get('inputs') != expected_inputs or
            type(record.get('bootstrap', {}).get('returncode')) is not int or
            record['bootstrap']['returncode'] != 0 or
            [case.get('mode') for case in record.get('cases', [])] != MODES):
        raise ValueError('Wine lifecycle qualification/input/case mismatch')
    for case in record['cases']:
        mode = case['mode']
        log = results / (mode + '.log')
        events = results / (mode + '-backend.log')
        if (type(case.get('returncode')) is not int or case['returncode'] != 0 or
                case.get('log') != log.name or 'PASS:' not in log.read_text() or
                case.get('module_root') != str(on_root) or case.get('backend_path') != str(backend) or
                case.get('pe_events') != expected_pe_events[mode] or
                (results / (mode + '-pe.log')).read_text().splitlines() != expected_pe_events[mode] or
                not isinstance(case.get('events'), list) or
                Counter(case['events']) != Counter(expected_events[mode]) or
                Counter(events.read_text().splitlines()) != Counter(expected_events[mode])):
            raise ValueError('Wine lifecycle probe/events mismatch: ' + mode)
    cleanup = record.get('cleanup', {})
    steps = cleanup.get('steps', [])
    if (cleanup.get('verified_absent') is not True or cleanup.get('lock', {}).get('absent') is not True or
            [step.get('command', [''])[-1] for step in steps] != ['-k', '-w', '-k0'] or
            any(step.get('accepted') is not True or type(step.get('returncode')) is not int or
                step['returncode'] not in codes
                for step, codes in zip(steps, [(0, 1), (0,), (1,)]))):
        raise ValueError('Wine lifecycle server cleanup not verified')
    for step, name in zip(steps, ['wineserver-cleanup', 'wineserver-wait', 'wineserver-verify']):
        log = results / (name + '.log')
        if not log.is_file() or step['returncode'] == 1 and log.read_text().strip():
            raise ValueError('Wine lifecycle cleanup log missing or has absence diagnostics')
    if not (results / 'bootstrap.log').is_file():
        raise ValueError('Wine lifecycle bootstrap log missing')
    return record


def inputs(tool, build):
    return {'on': {name: sha256(tool / 'files/lib/wine-cuda/zluda' / name) for name in cuda_package.MODULES},
            'off': {arch + '/nvcuda.dll': sha256(tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll')
                    for arch in cuda_package.policy.ARCHES.values()},
            'backend': sha256(build / 'libcuda.so.1'), 'probe': sha256(build / 'lifecycle.exe'),
            'helper': sha256(build / 'lifecycle-helper.dll'), 'base_artifact': base_artifact()}


def run(source, tool, build, work):
    if work.exists() or not all(path.is_absolute() for path in [source, tool, build, work]):
        raise ValueError('Wine lifecycle gate requires fresh absolute output paths')
    work.mkdir(parents=True)
    record = {'status': 'running', 'source_pin': LOCK['components']['zluda']}
    error = None
    env = server = None
    try:
        wine_cpu.admit_cpu_devices()
        fixture = wine_cpu.admit_source(source)
        wine, server = wine_cpu.admit_tool(source, tool)
        for name in ['tmp', 'runtime']:
            (work / name).mkdir(mode=0o700)
        env = wine_cpu.environment(tool, work, wine, server)
        for name in ['lifecycle.exe', 'lifecycle-helper.dll']:
            record[name] = wine_cpu.inspect_probe(build / name, 'win64')
        record['backend_needed'] = wine_cpu.inspect_backend(build / 'libcuda.so.1')
        before = inputs(tool, build)
        spec = importlib.util.spec_from_file_location('source_wine_lifecycle', fixture / 'run-lifecycle.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        wine_cpu.command(['python3', fixture / 'run-lifecycle.py', '--wine', wine, '--wineserver', server,
            '--dll-dir', tool / 'files/lib/wine', '--on-root', tool / 'files/lib/wine-cuda/zluda',
            '--off-root', tool / 'files/lib/wine-cuda/stub', '--backend', build / 'libcuda.so.1',
            '--probe', build / 'lifecycle.exe', '--helper', build / 'lifecycle-helper.dll',
            '--base-profile', PROFILE_FILE,
            '--prefix', work / 'prefix', '--results', work / 'results'], env, work / 'runner.log', 600)
        record['runner'] = check_result(work / 'results', before, runner.EXPECTED_EVENTS, runner.EXPECTED_PE_EVENTS,
                                        tool / 'files/lib/wine-cuda/zluda', build / 'libcuda.so.1')
        if inputs(tool, build) != before:
            raise ValueError('Wine lifecycle changed immutable inputs')
    except BaseException as caught:
        error = caught
        record['error'] = str(caught)
    finally:
        if env is not None and (work / 'prefix').exists():
            record['cleanup'] = wine_cpu.cleanup_server(server, env, work)
            if record['cleanup']['status'] != 'passed':
                error = error or ValueError('Wine lifecycle outer server cleanup failed')
        record['status'] = 'failed' if error else 'passed'
        if error:
            record['error'] = str(error)
        (work / 'INTEGRATION.json').write_text(json.dumps(record, indent=2) + '\n')
    if error:
        raise error
    return record
