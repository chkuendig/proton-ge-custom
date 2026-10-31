#!/usr/bin/env python3
"""Reviewer-side prepared-source audit; production receives only ABI-2 receipt."""
import importlib.util
import json
from pathlib import Path
import re

from inputs import HERE, LOCK, git, sha256
import wine_provider

REVIEW = HERE / 'reviewer/wine'
FLAGS = ['callback_id_zero', 'same_thread_destructor', 'process_exit', 'thread_exit',
         'loader_lock_refusal', 'locally_scoped_strong_provider', 'eager_binding']


def audit_com(source):
    contract = json.loads((REVIEW / 'd3d11-abi.json').read_text())
    definitions, identities = {}, {}
    for name in contract['source_files_sha256']:
        path = source / 'include' / name
        text = path.read_text()
        identities['include/' + name] = sha256(path)
        for match in re.finditer(r'interface\s+(\w+)\s*:\s*(\w+)\s*\{(.*?)\n\}', text, re.S):
            attrs = text[text.rfind('[', 0, match.start()):match.start()]
            uuid = re.search(r'uuid\(([^)]+)\)', attrs, re.I)
            methods = re.findall(r'\b(\w+)\s*\([^;{}]*\)\s*;', match[3])
            definitions[match[1]] = (match[2], methods, uuid[1].lower() if uuid else None)
    def methods(name):
        if name == 'IUnknown':
            return ['QueryInterface', 'AddRef', 'Release']
        parent, own, _ = definitions[name]
        return methods(parent) + own
    for name, spec in contract['interfaces'].items():
        actual = methods(name)
        if definitions[name][2] != spec['uuid'] or len(actual) != spec['vtable_words']:
            raise ValueError('prepared COM layout/UUID changed: ' + name)
        for method, slot in spec['methods'].items():
            if actual.index(method) != slot:
                raise ValueError('prepared COM slot changed: ' + name + '/' + method)
    return identities


def produce(source, sdk, output):
    # These source/body comparisons remain reviewer-only, including diagnostics
    # containing full failed definitions. Production receives only the pinned result.
    git(source, 'merge-base', '--is-ancestor', LOCK['components']['wine'], 'HEAD')
    spec = importlib.util.spec_from_file_location('reviewer_wine_callback', REVIEW / 'prepared_source.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    report, artifacts = checker.audit(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    checker.write_report(output.with_name('WINE-SOURCE-AUDIT.json'), report, artifacts)
    if not report['attested']:
        raise ValueError('prepared Wine body/layout audit failed: ' + '; '.join(report['errors']))
    prepared = dict(report['files'], **audit_com(source))
    winnt = source / 'include/winnt.h'
    for name, value in [('DLL_PROCESS_DETACH', 0), ('DLL_PROCESS_ATTACH', 1),
                        ('DLL_THREAD_ATTACH', 2), ('DLL_THREAD_DETACH', 3)]:
        if not re.search(r'^\s*#\s*define\s+' + name + r'\s+' + str(value) + r'\b', winnt.read_text(), re.M):
            raise ValueError('prepared Wine detach/TLS reason changed: ' + name)
    prepared['include/winnt.h'] = sha256(winnt)
    provider = wine_provider.inspect(sdk / 'lib/ntdll.so')
    record = {'contract': 'zluda-wine-prepared-abi-2', 'wine_pin': LOCK['components']['wine'],
              'prepared_commit': git(source, 'rev-parse', 'HEAD'),
              'verified': dict.fromkeys(FLAGS, True), 'prepared_files': prepared,
              'sdk_files': {name: sha256(sdk / name) for name in ['bin/winebuild', 'lib/ntdll.so']},
              'provider': provider,
              'review_contracts': {p.name: sha256(p) for p in REVIEW.glob('*.json')},
              'review': 'Exact callback/stack/exit/TLS/loader-lock bodies and layouts; prepared COM slots; local strong provider; Wine eager Unixlib binding. Full source review stays outside production.'}
    output.write_text(json.dumps(record, indent=2) + '\n')
    return record
