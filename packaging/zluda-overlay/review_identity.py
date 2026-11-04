#!/usr/bin/env python3
"""CPU-only paired compiler screen; inputs must be public tracked PTX."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def screen(source, capsules, dest):
    source, capsules, dest = map(Path, (source, capsules, dest))
    dest.mkdir(parents=True, exist_ok=True)
    inputs = subprocess.check_output(['git', '-C', str(source), 'ls-files', '*.ptx'], text=True).splitlines()
    arms = ['baseline', 'fix', 'without-image', 'without-const-zero']
    rows = []
    for index, name in enumerate(inputs):
        path = source / name
        row = {'input': name, 'input_sha256': sha(path), 'arms': {}}
        for arm in arms:
            out = dest / 'outputs' / str(index) / arm
            out.mkdir(parents=True)
            result = subprocess.run([str(capsules / arm / 'zoc'), '--arch', 'gfx1030',
                                     '--output-dir', str(out), str(path)],
                                    capture_output=True, timeout=900)
            (out / 'stderr.txt').write_bytes(result.stderr)
            (out / 'stdout.txt').write_bytes(result.stdout)
            row['arms'][arm] = {'rc': result.returncode, 'files': {
                p.name: sha(p) for p in sorted(out.iterdir()) if p.suffix not in ['.txt']}}
        rows.append(row)
        with (dest / 'rows.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
        print(index + 1, '/', len(inputs), name, flush=True)
    comparisons = {}
    for arm in arms[1:]:
        differing, failures, identical, failed_both = [], [], 0, 0
        for row in rows:
            a, b = row['arms']['baseline'], row['arms'][arm]
            if a['rc'] != b['rc'] or set(a['files']) != set(b['files']):
                failures.append(row['input'])
            diffs = [name for name in set(a['files']) & set(b['files']) if a['files'][name] != b['files'][name]]
            if diffs:
                differing.append({'input': row['input'], 'files': diffs})
            elif a == b:
                if a['rc'] == 0:
                    identical += 1
                else:
                    failed_both += 1
        comparisons[arm] = {'identical_success': identical, 'same_failure': failed_both,
                            'different_outputs': differing, 'different_status_or_files': failures,
                            'all_outputs_identical': not differing and not failures}
    record = {'inputs': len(inputs), 'architecture': 'gfx1030', 'GPU_launches': 0,
              'comparisons': comparisons}
    (dest / 'RESULT.json').write_text(json.dumps(record, indent=2) + '\n')
    # Removal trials are evidence, not landing gates. The shipping fix must match.
    assert comparisons['fix']['all_outputs_identical'], comparisons['fix']
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('source')
    parser.add_argument('capsules')
    parser.add_argument('dest')
    args = parser.parse_args()
    print(json.dumps(screen(args.source, args.capsules, args.dest), indent=2))
