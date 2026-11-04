#!/usr/bin/env python3
"""Compare every shipped member against the immutable d32 product, fail closed."""
import argparse
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

from inputs import LOCK, LOCK_FILE, PROFILE, PROTON_ROOT, RECIPE_ROOT, components_for_part, bridge_component, bridge_root, git, packaging_identity, sha256

CONTROL_TAR = '4321f9b0e78e1314ef9ba62845f9f9bb9d79a4fb83de6ce54826802f8583edf0'
CONTROL_COMMIT = 'd32c45244f7f719d6e37c962c52b1cbdc063ce2c'
# Only these derived identity receipts can differ, never executable directories.
JSON_FIELDS = {
    'provenance/PROVENANCE.json': {'proton', 'component_build_proton', 'input_lock_sha256', 'wine_bridge', 'components', 'packaging'},
    'provenance/zluda.json': {'proton', 'input_lock_sha256', 'wine_bridge', 'components', 'packaging'},
    'provenance/bridges.json': {'proton', 'input_lock_sha256', 'wine_bridge', 'components', 'packaging'},
    'provenance/inputs.lock.json': {'proton_source', 'wine_bridge', 'components'},
    'provenance/SIZES.json': {'installed_regular_bytes', 'source_bytes', 'unique_content_bytes'},
    'provenance/zluda-SBOM.json': {'sources'},
    'provenance/bridges-SBOM.json': {'sources'},
    'provenance/ZLUDA-WINE.json': {'contract'},
    'provenance/WINE-CALLBACK-ATTESTATION.json': {'prepared_commit'},
    'provenance/WINE-SCOPE.json': {'baseline', 'head'},
}
SUPPORT = {'provenance/WINE-BUILD-RECIPE.mk', 'provenance/WINE-OVERLAY',
           'sources/zluda-wine.tar.gz.json', 'sources/zluda-wine.tar.gz.check.log',
           'external/prepared-wine-abi.json',
           'licenses/zluda-wine/LICENSES/PRODUCTION-INPUTS.json', 'provenance/WINE-BASELINE-COMMIT'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts:
        raise ValueError('unsafe archive member: ' + name)
    return p.as_posix().removeprefix('./')


def inventory(archive, product):
    entries, content = {}, {}
    with tarfile.open(fileobj=archive, mode='r|*') as stream:
        for member in stream:
            name = safe_name(member.name)
            if product:
                parts = name.split('/', 1)
                if len(parts) == 1:
                    continue
                name = parts[1]
            if name in entries:
                raise ValueError('duplicate archive member: ' + name)
            record = {'mode': member.mode, 'mtime': member.mtime, 'uid': member.uid,
                      'gid': member.gid, 'type': member.type.decode('ascii')}
            if member.isfile():
                h, saved = hashlib.sha256(), []
                retain = name.startswith(('sources/', 'external/proton-build-recipes/')) or name in JSON_FIELDS or name in SUPPORT or name in ('MANIFEST', 'SYMLINKS.json', 'external/BRIDGE-BUILD.json')
                with stream.extractfile(member) as source:
                    while block := source.read(1024 * 1024):
                        h.update(block)
                        if retain:
                            saved.append(block)
                record.update(sha256=h.hexdigest(), size=member.size)
                if retain:
                    content[name] = b''.join(saved)
            elif member.issym() or member.islnk():
                record['target'] = member.linkname
            elif not member.isdir():
                raise ValueError('unsupported archive member: ' + name)
            entries[name] = record
    def resolve(name, seen):
        if name in seen or name not in entries:
            raise ValueError('invalid archive hardlink: ' + name)
        record = entries[name]
        if record['type'] == '1':
            target = safe_name(record['target'])
            if product:
                target = target.split('/', 1)[-1]
            source = resolve(target, seen | {name})
            record.update(sha256=source['sha256'], size=source['size'])
        elif 'sha256' not in record:
            raise ValueError('archive hardlink target is not a regular file: ' + name)
        return record
    for name, record in entries.items():
        if record['type'] == '1':
            resolve(name, set())
    return entries, content


def verify_manifest(entries, content, external):
    manifest = content['MANIFEST']
    if manifest != external.read_bytes():
        raise ValueError('external MANIFEST differs from packaged MANIFEST')
    declared = {}
    for line in manifest.decode().splitlines():
        digest, name = line.split(None, 1)
        name = safe_name(name.lstrip('*'))
        if name in declared:
            raise ValueError('duplicate MANIFEST path')
        declared[name] = digest
    actual = {n: r['sha256'] for n, r in entries.items() if 'sha256' in r and n != 'MANIFEST'}
    if declared != actual:
        raise ValueError('MANIFEST does not describe actual shipped regular files')
    links = json.loads(content['SYMLINKS.json'])
    if links != {n: r['target'] for n, r in entries.items() if r['type'] == '2'}:
        raise ValueError('SYMLINKS.json differs from actual shipped links')
    return sha(manifest)


def archive_commit(data):
    with tarfile.open(fileobj=io.BytesIO(data), mode='r|*') as stream:
        next(iter(stream), None)
        commit = stream.pax_headers.get('comment', '')
    return commit if re.fullmatch('[0-9a-f]{40}', commit) and commit != '0' * 40 else None


def metadata_change(name, before, after, contents=None, old_entries=None, new_entries=None):
    try:
        old, new = json.loads(before), json.loads(after)
        if not isinstance(old, dict) or not isinstance(new, dict):
            return None
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        if not changed <= JSON_FIELDS[name]:
            return None
        expected = {}
        if name == 'provenance/inputs.lock.json':
            if new != LOCK:
                return None
        elif name in {'provenance/PROVENANCE.json', 'provenance/zluda.json', 'provenance/bridges.json'}:
            part = name.removeprefix('provenance/').removesuffix('.json')
            components = LOCK['components'] if part == 'PROVENANCE' else {
                n: LOCK['components'][n] for n in components_for_part(part)}
            expected = {'proton': PROFILE['source_commit'], 'component_build_proton': PROFILE['source_commit'],
                        'input_lock_sha256': sha(LOCK_FILE.read_bytes()),
                        'wine_bridge': LOCK['wine_bridge'], 'components': components, 'packaging': packaging_identity()}
        elif name.endswith('-SBOM.json'):
            part = name.removeprefix('provenance/').removesuffix('-SBOM.json')
            expected = {'sources': {n: LOCK['components'][n] for n in components_for_part(part)}}
        elif name == 'provenance/ZLUDA-WINE.json':
            contract = dict(old['contract'], source_commit=LOCK['components'][bridge_component()])
            expected = {'contract': contract}
        elif name == 'provenance/WINE-CALLBACK-ATTESTATION.json':
            commit = archive_commit((contents or {})['sources/wine-prepared.tar.gz'])
            if not commit:
                return None
            expected = {'prepared_commit': commit}
        elif name == 'provenance/WINE-SCOPE.json':
            commit = archive_commit((contents or {})['sources/wine-prepared.tar.gz'])
            # A changed baseline requires a verified prepared-commit receipt.
            baseline = baseline_commit(contents or {})
            if not commit or 'baseline' in changed and not baseline:
                return None
            expected = {'head': commit, 'baseline': baseline or old.get('baseline')}
        elif name == 'provenance/SIZES.json':
            if old_entries is None or new_entries is None:
                return None
            def totals(entries):
                records = {n:r for n,r in entries.items() if 'sha256' in r and
                           n not in {'MANIFEST', 'SYMLINKS.json', 'provenance/SIZES.json'}}
                return {'installed_regular_bytes': sum(r['size'] for r in records.values()),
                        'source_bytes': sum(r['size'] for n,r in records.items() if n.startswith('sources/')),
                        'unique_content_bytes': sum({r['sha256']:r['size'] for r in records.values()}.values())}
            a, b = totals(old_entries), totals(new_entries)
            expected = {k:old[k] + b[k] - a[k] for k in a}
        required = JSON_FIELDS[name] - ({'component_build_proton'} if name != 'provenance/PROVENANCE.json' else set())
        if name != 'provenance/inputs.lock.json' and any(key not in expected or new.get(key) != expected[key] for key in required):
            return None
        return {'classification': 'bound-source-or-derived-metadata', 'changed_fields': sorted(changed)}
    except (ValueError, TypeError, KeyError):
        return None


def baseline_commit(contents):
    try:
        raw = contents['provenance/WINE-BASELINE-COMMIT']
        facts = PROFILE['wine_prepared_baseline']
        scope = json.loads(contents['provenance/WINE-SCOPE.json'])
        headers, message = raw.decode('utf-8').split('\n\n', 1)
        rows = headers.splitlines()
        identity = r'(author|committer) Christian Kündig <christian@kuendig.info> [0-9]+ [+-][0-9]{4}'
        if (len(rows) != 4 or rows[0] != 'tree ' + facts['tree'] or
                rows[1] != 'parent ' + LOCK['components']['wine'] or
                re.fullmatch(identity, rows[2]) is None or not rows[2].startswith('author ') or
                re.fullmatch(identity, rows[3]) is None or not rows[3].startswith('committer ') or
                message != 'prep/base-' + LOCK['ge_source'][:12] + '\n\nAssisted-by: Codex:gpt-6.1-sol\n'):
            return None
        if (facts['wine_pin'] != LOCK['components']['wine'] or
                facts['staging_pin'] != LOCK['components']['wine-staging'] or
                facts['base_source'] != LOCK['ge_source'] or
                b'\nbase_inputs=' + facts['base_inputs'].encode() + b'\n' not in contents['provenance/WINE-OVERLAY']):
            return None
        digest = hashlib.sha1(b'commit ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        return digest if digest == scope['baseline'] else None
    except (KeyError, ValueError, TypeError, UnicodeError):
        return None


def production_inventory(before, after):
    try:
        old, new = json.loads(before), json.loads(after)
        expected = dict(old)
        expected['source_inputs'] = dict(old['source_inputs'])
        del expected['source_inputs']['wine/zluda_wine/build-pilot.sh']
        if new == expected:
            return {'classification': 'retired-test-tool-inventory-row'}
    except (ValueError, TypeError, KeyError):
        pass
    return None


def cargo_observations(before, after, old_inventory=None, new_inventory=None):
    if before == after:
        return True
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        current = dict(new)
        output = current.pop('build_output', None)
        if output is None or not current['dep_info'].endswith('/output') or sha(output.encode()) != current['sha256']:
            if old != new:
                return False
            continue
        digest = current.pop('sha256')
        previous = dict(old)
        old_digest = previous.pop('sha256')
        if current != previous:
            return False
        if digest == old_digest:
            continue
        if not production_inventory(old_inventory, new_inventory):
            return False
        old_count = len(json.loads(old_inventory)['source_inputs'])
        new_count = len(json.loads(new_inventory)['source_inputs'])
        record = lambda count: json.dumps({'production_input_closure': 'passed', 'files': count,
                                          'adapter_headers': ['stdint.h', 'stddef.h']}) + '\n'
        expected = record(new_count)
        lines = [line for line in output.splitlines(keepends=True) if '"production_input_closure"' in line]
        if lines != [expected] or sha(output.replace(expected, record(old_count), 1).encode()) != old_digest:
            return False
    return True


def backend_observations(before, after, bridge_before, bridge_after, old_inventory, new_inventory):
    if before == after:
        return True
    if not cargo_observations(bridge_before, bridge_after, old_inventory, new_inventory):
        return False
    pairs = {}
    for old, new in zip(bridge_before, bridge_after):
        name = old['dep_info']
        if name in pairs:
            return False
        pairs[name] = (old['sha256'], new['sha256'])
    if len(before) != len(after):
        return False
    for old, new in zip(before, after):
        if old == new:
            continue
        a, b = dict(old), dict(new)
        digests = (a.pop('sha256'), b.pop('sha256'))
        if a != b or not a['dep_info'].endswith('/output') or pairs.get(a['dep_info']) != digests:
            return False
    return True


def recipe_change(relative, after, record=None, capsule=False):
    spec = 'HEAD:packaging/zluda-overlay/' + relative
    try:
        kind = git(RECIPE_ROOT, 'cat-file', '-t', spec)
        if kind == 'tree':
            return {'classification': 'moved-packaging-source-directory'} if (
                after is None and record and record['type'] == '5') else None
        data = subprocess.check_output(['git', '-C', str(RECIPE_ROOT), 'show', spec])
        mode = git(RECIPE_ROOT, 'ls-tree', 'HEAD', '--', 'packaging/zluda-overlay/' + relative).split()[0]
        if after is not None and kind == 'blob' and sha(data) == sha(after) and (record is None or
                record['type'] == '0' and record['mode'] == (0o755 if mode == '100755' and not capsule else 0o644)):
            return {'classification': 'moved-packaging-tool-source',
                    'source_blob': git(RECIPE_ROOT, 'rev-parse', spec)}
    except subprocess.CalledProcessError:
        if after is not None:
            return None
        origin = json.loads((RECIPE_ROOT / 'SOURCE-ORIGIN.json').read_text())['files']
        retired = [x for x in origin if x['path'] == 'packaging/zluda-overlay/' + relative or x['path'].startswith('packaging/zluda-overlay/' + relative.rstrip('/') + '/')]
        if retired and all(x['action'] in {'MOVE-PRIVATE', 'DROP'} for x in retired):
            return {'classification': 'removed-reviewed-test-tool-source'}
    return None


def prepared_identity(before, after, expected=None):
    old, new = json.loads(before), json.loads(after)
    changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    if changed <= {'prepared_commit'} and expected and new.get('prepared_commit') == expected:
        return {'classification': 'prepared-source-commit-metadata', 'changed_fields': sorted(changed)}
    return None


def support_change(name, before, after, contents, control_contents=None):
    if name == 'provenance/WINE-BASELINE-COMMIT':
        commit = baseline_commit(contents)
        return {'classification': 'verified-prepared-baseline-commit', 'commit': commit} if commit else None
    if name == 'licenses/zluda-wine/LICENSES/PRODUCTION-INPUTS.json':
        return production_inventory(before, after)
    if name == 'provenance/WINE-BUILD-RECIPE.mk':
        import wine_deps
        scope = json.loads(contents['provenance/WINE-SCOPE.json'])
        with tempfile.TemporaryDirectory() as temporary:
            build = Path(temporary)
            expected = wine_deps.generate(PROTON_ROOT, build, scope['mode'] == 'x86_64-unix-ntdll').read_text()
            expected = expected.replace(str(PROTON_ROOT), '/src').replace(str(build), '/work/build/wine')
            expected = expected.replace(str(wine_deps.HERE), '/recipes/packaging/zluda-overlay')
        if after == expected.encode():
            return {'classification': 'generated-committed-build-tooling', 'sha256': sha(after)}
    elif name == 'provenance/WINE-OVERLAY':
        normalize = lambda data: re.sub(rb'^built=\S+', b'built=<timestamp>', data, flags=re.M)
        old, new = normalize(before), normalize(after)
        expected = sha(contents['provenance/WINE-BUILD-RECIPE.mk']).encode()
        if re.search(rb'^rev=' + PROFILE['source_commit'].encode() + rb'$', new, re.M) is None:
            return None
        if re.search(rb'^configure_recipe_sha256=' + expected + rb'$', new, re.M) is None:
            return None
        old = re.sub(rb'^(rev|configure_recipe_sha256)=\S+', rb'\1=<identity>', old, flags=re.M)
        new = re.sub(rb'^(rev|configure_recipe_sha256)=\S+', rb'\1=<identity>', new, flags=re.M)
        if old == new:
            return {'classification': 'build-tooling-identity-and-timestamp'}
    elif name == 'sources/zluda-wine.tar.gz.check.log':
        def normalize(data):
            data = re.sub(rb'/archive-check-[a-z0-9_]+/', b'/archive-check-<temporary>/', data)
            data = re.sub(rb'^(    Finished .+ in )[0-9.]+s$', rb'\1<duration>s', data, flags=re.M)
            lines = data.splitlines(keepends=True)
            count = 0
            while count < len(lines) and re.fullmatch(rb'   Compiling [A-Za-z0-9_-]+ v[^\n]+\n', lines[count]):
                count += 1
            return b''.join(sorted(lines[:count]) + lines[count:])
        if normalize(before) == normalize(after):
            return {'classification': 'source-rebuild-log-compile-order-temporary-path-and-duration'}
    elif name == 'sources/zluda-wine.tar.gz.json':
        old, new = json.loads(before), json.loads(after)
        changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
        if not changed <= {'source_commit', 'source_tree', 'prepared_abi_receipt_sha256', 'sdk_capsule', 'archive_sha256', 'cargo_inputs', 'backend_source'}:
            return None
        if (
                new.get('source_commit') != LOCK['components'][bridge_component()] or
                new.get('source_tree') != git(bridge_root(PROTON_ROOT), 'rev-parse', LOCK['components'][bridge_component()] + '^{tree}')):
            return None
        inventory_path = 'licenses/zluda-wine/LICENSES/PRODUCTION-INPUTS.json'
        if not cargo_observations(old.get('cargo_inputs', []), new.get('cargo_inputs', []),
                                  (control_contents or {}).get(inventory_path), contents.get(inventory_path)):
            return None
        previous_backend, current_backend = dict(old.get('backend_source', {})), dict(new.get('backend_source', {}))
        previous_records = previous_backend.pop('backend_cargo_inputs', [])
        current_records = current_backend.pop('backend_cargo_inputs', [])
        if previous_backend != current_backend or not backend_observations(
                previous_records, current_records, old.get('cargo_inputs', []), new.get('cargo_inputs', []),
                (control_contents or {}).get(inventory_path), contents.get(inventory_path)):
            return None
        capsule = contents['sources/zluda-wine.tar.gz']
        entries, data = inventory(io.BytesIO(capsule), False)
        expected = {n:r['sha256'] for n,r in entries.items() if n.startswith('external/') and 'sha256' in r}
        if (new['archive_sha256'] == sha(capsule) and new['sdk_capsule'] == expected and
                new['prepared_abi_receipt_sha256'] == sha(data['external/prepared-wine-abi.json'])):
            return {'classification': 'verified-source-capsule-receipt', 'changed_fields': sorted(changed)}
    return None


WINE_SOURCE = None


def nested_changes(name, before, after, control_contents=None, candidate_contents=None):
    old, oc = inventory(io.BytesIO(before), False)
    new, nc = inventory(io.BytesIO(after), False)
    changes, bad = [], []
    for path in sorted(old.keys() | new.keys()):
        a, b = old.get(path), new.get(path)
        if a == b:
            continue
        # The git archive commit epoch is metadata. Source bytes, modes and link
        # identities must still match unless an individually admitted test/tool.
        stripped = lambda r: {k: v for k, v in (r or {}).items() if k != 'mtime'}
        classification = None
        if a and b and stripped(a) == stripped(b):
            classification = {'classification': 'git-archive-commit-timestamp'}
        elif path.startswith('external/proton-build-recipes/'):
            classification = recipe_change(path.removeprefix('external/proton-build-recipes/'), nc.get(path), b, capsule=True)
        elif path == 'wine/zluda_wine/LICENSES/PRODUCTION-INPUTS.json':
            classification = production_inventory(oc.get(path), nc.get(path))
        elif path == 'external/BRIDGE-BUILD.json':
            x, y = json.loads(oc[path]), json.loads(nc[path])
            changed = {key for key in x.keys() | y.keys() if x.get(key) != y.get(key)}
            if (changed <= {'source_commit', 'source_tree', 'abi_receipt_sha256', 'backend_cargo_inputs'} and
                    y['abi_receipt_sha256'] == sha(nc['external/prepared-wine-abi.json']) and
                    y['source_commit'] == LOCK['components'][bridge_component()] and
                    ('source_tree' not in y or y['source_tree'] == git(bridge_root(PROTON_ROOT), 'rev-parse', LOCK['components'][bridge_component()] + '^{tree}'))):
                classification = {'classification': 'bridge-source-identity', 'changed_fields': sorted(changed)}
                if 'backend_cargo_inputs' in changed:
                    previous = json.loads((control_contents or {}).get('sources/zluda-wine.tar.gz.json', b'{}'))
                    current = json.loads((candidate_contents or {}).get('sources/zluda-wine.tar.gz.json', b'{}'))
                    inventory_path = 'licenses/zluda-wine/LICENSES/PRODUCTION-INPUTS.json'
                    if not backend_observations(x['backend_cargo_inputs'], y['backend_cargo_inputs'],
                            previous.get('cargo_inputs', []), current.get('cargo_inputs', []),
                            (control_contents or {}).get(inventory_path), (candidate_contents or {}).get(inventory_path)):
                        classification = None
        elif path == 'external/prepared-wine-abi.json':
            classification = prepared_identity(oc[path], nc[path], archive_commit(WINE_SOURCE) if WINE_SOURCE else None)
        else:
            scope = json.loads((RECIPE_ROOT / 'BRIDGE-ORIGIN.json').read_text())
            admitted = scope['files'].get(path, {})
            if admitted.get('action') in {'SHRINK', 'MOVE-PRIVATE', 'DROP'}:
                if b is None and admitted['action'] in {'MOVE-PRIVATE', 'DROP'}:
                    classification = {'classification': 'removed-private-test-fixture-tool'}
                elif b and 'sha256' in b:
                    try:
                        blob = subprocess.check_output(['git', '-C', str(bridge_root(PROTON_ROOT)), 'show', LOCK['components'][bridge_component()] + ':' + path])
                        if sha(blob) == b['sha256']:
                            classification = {'classification': 'compacted-admitted-test-fixture-tool', 'source_sha256': b['sha256']}
                    except subprocess.CalledProcessError:
                        pass
        if classification and a and b and any(a.get(k) != b.get(k) for k in ['type', 'mode', 'uid', 'gid', 'target']):
            classification = None
        entry = {'path': path, 'before': a, 'after': b, **(classification or {'classification': 'UNCLASSIFIED'})}
        changes.append(entry)
        if classification is None:
            bad.append(path)
    return {'classification': 'source-support-capsule', 'member_changes': changes, 'unclassified': bad}


def compare(control, candidate, cm, nm, output):
    if sha256(control) != CONTROL_TAR:
        raise ValueError('control is not published d32c4524 product')
    with control.open('rb') as a, candidate.open('rb') as b:
        old, oc = inventory(a, True)
        new, nc = inventory(b, True)
    global WINE_SOURCE
    WINE_SOURCE = nc.get('sources/wine-prepared.tar.gz')
    proof = {'go_ref': 'e1c6a6e3-20261104-proton-cleanup-p2', 'control_commit': CONTROL_COMMIT,
             'control_archive_sha256': CONTROL_TAR,
             'candidate_archive_sha256': sha256(candidate),
             'control_manifest_sha256': verify_manifest(old, oc, cm),
             'candidate_manifest_sha256': verify_manifest(new, nc, nm),
             'identical_members': 0, 'differences': [], 'unclassified': []}
    for name in sorted(old.keys() | new.keys()):
        a, b = old.get(name), new.get(name)
        if a == b:
            proof['identical_members'] += 1
            continue
        classification = None
        if name == 'MANIFEST':
            classification = {'classification': 'derived-verified-manifest'}
        elif name in JSON_FIELDS and name in oc and name in nc:
            classification = metadata_change(name, oc[name], nc[name], nc, old, new)
        elif name in SUPPORT and name in nc and (name in oc or name == 'provenance/WINE-BASELINE-COMMIT'):
            classification = support_change(name, oc.get(name), nc[name], nc, oc)
            if a is None and (b['type'] != '0' or b['mode'] != 0o644 or b['uid'] != 0 or b['gid'] != 0 or
                              b['mtime'] != int(PROFILE['source_date_epoch'])):
                classification = None
        elif name.startswith('sources/overlay-recipes/'):
            classification = recipe_change(name.removeprefix('sources/overlay-recipes/'), nc.get(name), b)
        elif name.startswith('sources/') and name.endswith('.tar.gz') and name in oc and name in nc:
            classification = nested_changes(name, oc[name], nc[name], oc, nc)
            if classification['unclassified']:
                proof['unclassified'].append(name)
        if classification and a and b and any(a.get(k) != b.get(k) for k in ['type', 'mode', 'uid', 'gid', 'mtime', 'target']):
            classification = None
        entry = {'path': name, 'before': a, 'after': b, **(classification or {'classification': 'UNCLASSIFIED'})}
        proof['differences'].append(entry)
        if classification is None:
            proof['unclassified'].append(name)
    proof['status'] = 'failed' if proof['unclassified'] else 'passed'
    output.write_text(json.dumps(proof, indent=2) + '\n')
    if proof['unclassified']:
        raise ValueError('unclassified shipping differences: ' + ', '.join(proof['unclassified']))
    print('Whole product identity PASS:', proof['identical_members'], 'identical members;', len(proof['differences']), 'classified source/metadata differences')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    for arg in ['control', 'candidate', 'control-manifest', 'candidate-manifest', 'output']:
        parser.add_argument('--' + arg, type=Path, required=True)
    a = parser.parse_args()
    compare(a.control, a.candidate, a.control_manifest, a.candidate_manifest, a.output)
