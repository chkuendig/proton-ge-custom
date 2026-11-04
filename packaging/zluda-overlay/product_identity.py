#!/usr/bin/env python3
"""Compare every shipped member against the immutable d32 product, fail closed."""
import argparse
import hashlib
import io
import json
import subprocess
from pathlib import Path, PurePosixPath
import tarfile

from inputs import HERE, LOCK, PROTON_ROOT, RECIPE_ROOT, bridge_root, git

CONTROL_TAR = '4321f9b0e78e1314ef9ba62845f9f9bb9d79a4fb83de6ce54826802f8583edf0'
CONTROL_COMMIT = 'd32c45244f7f719d6e37c962c52b1cbdc063ce2c'
# Only these derived identity receipts can differ, never executable directories.
JSON_FIELDS = {
    'provenance/PROVENANCE.json': {'proton', 'component_build_proton', 'input_lock_sha256', 'wine_bridge', 'components'},
    'provenance/zluda.json': {'proton', 'input_lock_sha256', 'wine_bridge', 'components', 'packaging'},
    'provenance/bridges.json': {'proton', 'input_lock_sha256', 'wine_bridge', 'components', 'packaging'},
    'provenance/inputs.lock.json': {'proton_source', 'wine_bridge', 'components'},
    'provenance/SIZES.json': {'installed_regular_bytes', 'source_bytes', 'unique_content_bytes'},
    'provenance/zluda-SBOM.json': {'sources', 'license_files'},
    'provenance/bridges-SBOM.json': {'sources', 'license_files'},
    'provenance/ZLUDA-WINE.json': {'source_commit', 'source_tree', 'build_contract_sha256'},
}


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
            record = {'mode': member.mode, 'mtime': member.mtime, 'type': member.type.decode('ascii')}
            if member.isfile():
                h, saved = hashlib.sha256(), []
                retain = name.startswith(('sources/', 'external/proton-build-recipes/')) or name in JSON_FIELDS or name in ('MANIFEST', 'SYMLINKS.json', 'external/BRIDGE-BUILD.json')
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


def metadata_change(name, before, after):
    try:
        old, new = json.loads(before), json.loads(after)
    except (ValueError, TypeError):
        return None
    changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    if changed <= JSON_FIELDS[name]:
        return {'classification': 'source-identity-or-derived-metadata', 'changed_fields': sorted(changed)}
    return None


def recipe_change(relative, after):
    path = HERE / relative
    # Candidate source bytes must be precisely those committed in the packaging
    # input, not arbitrary bytes in a generated tool source directory.
    if after is None and path.is_dir():
        return {'classification': 'moved-packaging-source-directory'}
    if after is not None and path.is_file() and sha(path.read_bytes()) == sha(after):
        try:
            expected = git(RECIPE_ROOT, 'rev-parse', 'HEAD:packaging/zluda-overlay/' + relative)
            if git(RECIPE_ROOT, 'hash-object', str(path)) == expected:
                return {'classification': 'moved-packaging-tool-source', 'source_blob': expected}
        except Exception:
            return None
    if after is None and not path.exists():
        origin = json.loads((RECIPE_ROOT / 'SOURCE-ORIGIN.json').read_text())['files']
        retired = [x for x in origin if x['path'] == 'packaging/zluda-overlay/' + relative or x['path'].startswith('packaging/zluda-overlay/' + relative.rstrip('/') + '/')]
        if retired and all(x['action'] in {'MOVE-PRIVATE', 'DROP'} for x in retired):
            return {'classification': 'removed-reviewed-test-tool-source'}
    return None


def nested_changes(name, before, after):
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
            classification = recipe_change(path.removeprefix('external/proton-build-recipes/'), nc.get(path))
        elif path == 'external/BRIDGE-BUILD.json':
            x, y = json.loads(oc[path]), json.loads(nc[path])
            changed = {key for key in x.keys() | y.keys() if x.get(key) != y.get(key)}
            if changed <= {'source_commit', 'source_tree'}:
                classification = {'classification': 'bridge-source-identity', 'changed_fields': sorted(changed)}
        else:
            scope = json.loads((RECIPE_ROOT / 'BRIDGE-ORIGIN.json').read_text())
            admitted = scope.get(path, {})
            if admitted.get('action') in {'SHRINK', 'MOVE-PRIVATE', 'DROP'}:
                if b is None and admitted['action'] in {'MOVE-PRIVATE', 'DROP'}:
                    classification = {'classification': 'removed-private-test-fixture-tool'}
                elif b and 'sha256' in b:
                    try:
                        blob = subprocess.check_output(['git', '-C', str(bridge_root(PROTON_ROOT)), 'show', 'HEAD:' + path])
                        if sha(blob) == b['sha256']:
                            classification = {'classification': 'compacted-admitted-test-fixture-tool', 'source_sha256': b['sha256']}
                    except subprocess.CalledProcessError:
                        pass
        entry = {'path': path, 'before': a, 'after': b, **(classification or {'classification': 'UNCLASSIFIED'})}
        changes.append(entry)
        if classification is None:
            bad.append(path)
    return {'classification': 'source-support-capsule', 'member_changes': changes, 'unclassified': bad}


def compare(control, candidate, cm, nm, output):
    if hashlib.file_digest(control.open('rb'), 'sha256').hexdigest() != CONTROL_TAR:
        raise ValueError('control is not published d32c4524 product')
    with control.open('rb') as a, candidate.open('rb') as b:
        old, oc = inventory(a, True)
        new, nc = inventory(b, True)
    proof = {'go_ref': 'e1c6a6e3-20261104-proton-cleanup-p2', 'control_commit': CONTROL_COMMIT,
             'control_archive_sha256': CONTROL_TAR,
             'candidate_archive_sha256': hashlib.file_digest(candidate.open('rb'), 'sha256').hexdigest(),
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
            classification = metadata_change(name, oc[name], nc[name])
        elif name.startswith('sources/overlay-recipes/'):
            classification = recipe_change(name.removeprefix('sources/overlay-recipes/'), nc.get(name))
        elif name.startswith('sources/') and name.endswith('.tar.gz') and name in oc and name in nc:
            classification = nested_changes(name, oc[name], nc[name])
            if classification['unclassified']:
                proof['unclassified'].append(name)
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
