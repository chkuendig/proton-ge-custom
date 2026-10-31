#!/usr/bin/env python3
"""Fail closed on changes to Wine's private same-thread callback/stack ABI.

Token-exact ABI fragments allow unrelated GE hotfixes. Every relied-on body,
signature and layout prefix must remain identical; names alone are insufficient.
Full prepared-file hashes are retained as receipts, not compared to raw Wine.
Failures retain the full prepared definitions and source, before exiting nonzero.
"""
import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re

CONTRACT = Path(__file__).with_name('wine-callback-abi.json')
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*[\s\S]*?\*/|//[^\n]*|[A-Za-z_][A-Za-z_0-9]*|[0-9][A-Za-z_0-9]*|[^\s]')


class AttestationError(ValueError):
    def __init__(self, report, artifacts):
        super().__init__('\n'.join(report['errors']))
        self.report = report
        self.artifacts = artifacts


def token_spans(source):
    """Keep original offsets, including comments and spliced macro lines."""
    removed = []
    pieces = []
    end = 0
    for match in re.finditer(r'\\\n', source):
        pieces.append(source[end:match.start()])
        removed.append(match.start()-2*len(removed))
        end = match.end()
    pieces.append(source[end:])
    normalized = ''.join(pieces)
    rows = []
    for match in TOKEN.finditer(normalized):
        value = match.group()
        if not value.startswith(('/*','//')):
            start = match.start()+2*bisect_right(removed, match.start())
            end = match.end()+2*bisect_right(removed, match.end())
            rows.append((value, start, end))
    return rows


def tokens(source):
    # Consume string/character literals before comments; comment-like text in
    # assembly strings must never disappear from this ABI contract.
    return [row[0] for row in token_spans(source)]


def fragment_start(source, item):
    begin = tokens(item['begin'])
    positions = [i for i in range(len(source)-len(begin)+1) if source[i:i+len(begin)]==begin]
    if len(positions)!=1:
        raise ValueError('missing/ambiguous Wine ABI fragment: '+item['name'])
    return positions[0]


def fragment(source, item):
    start = fragment_start(source, item)
    return source[start:start+item['tokens']]


def capture_end(source, start, item):
    # Diagnostics retain a complete changed function/assembly definition even
    # when an insertion makes it longer than the strict comparison window.
    delimiters = {'body': ('{','}'), 'call': ('(',')')}
    if item.get('capture') not in delimiters:
        return min(len(source), start+item['tokens'])
    opening, closing = delimiters[item['capture']]
    depth = 0
    for i in range(start, len(source)):
        if source[i]==opening:
            depth += 1
        elif source[i]==closing and depth:
            depth -= 1
            if not depth:
                return i+1
    raise ValueError('unterminated Wine ABI definition: '+item['name'])


def sha(value):
    return hashlib.sha256(value).hexdigest()


def audit(source, contract=None):
    record = json.loads(CONTRACT.read_text()) if contract is None else contract
    report = {'contract': record['contract'], 'wine_commit': record['wine_commit'],
              'callback_id': record['callback_id'], 'files': {}, 'sources': {},
              'fragments': [], 'errors': [], 'attested': False}
    report['contract_sha256'] = sha(json.dumps(record, sort_keys=True, separators=(',',':')).encode())
    if 'prepared_exit_provenance' in record:
        report['prepared_exit_provenance'] = record['prepared_exit_provenance']
    artifacts = {}
    for name, spec in record['files'].items():
        path = source/name
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-8')
        except (OSError, UnicodeError) as error:
            report['errors'].append('missing pinned Wine callback ABI source: '+name+': '+str(error))
            continue
        source_artifact = 'source/'+name
        artifacts[source_artifact] = raw
        report['files'][name] = sha(raw)
        report['sources'][name] = {'artifact': source_artifact, 'sha256': sha(raw)}
        spans = token_spans(text)
        source_tokens = [row[0] for row in spans]
        for item in spec['fragments']:
            row = {'file': name, 'name': item['name'], 'expected_sha256': item['sha256'],
                   'expected_tokens': item['tokens'], 'matches': False}
            report['fragments'].append(row)
            try:
                start = fragment_start(source_tokens, item)
                compared = source_tokens[start:start+item['tokens']]
                digest = sha('\n'.join(compared).encode())
                end = capture_end(source_tokens, start, item)
                first, last = spans[start][1], spans[end-1][2]
                captured = text[first:last].encode('utf-8')
                artifact = 'definitions/'+name+'/'+re.sub('[^A-Za-z0-9_-]', '-', item['name'])+'.txt'
                artifacts[artifact] = captured
                row.update(actual_sha256=digest, compared_tokens=len(compared),
                           artifact=artifact, raw_sha256=sha(captured),
                           captured_tokens=end-start,
                           captured_token_sha256=sha('\n'.join(source_tokens[start:end]).encode()),
                           first_line=text.count('\n', 0, first)+1,
                           last_line=text.count('\n', 0, last)+1,
                           matches=digest==item['sha256'] and len(compared)==item['tokens'])
                if not row['matches']:
                    raise ValueError('Wine callback ABI changed; review the prepared source: '+name+':'+item['name'])
            except ValueError as error:
                row['error'] = str(error)
                report['errors'].append(str(error))
    report['attested'] = not report['errors']
    return report, artifacts


def attest(source, contract=None):
    report, artifacts = audit(source, contract)
    if not report['attested']:
        raise AttestationError(report, artifacts)
    return report


def write_report(output, report, artifacts):
    directory = output.with_suffix('.fragments')
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in artifacts.items():
        path = directory/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    report['fragment_directory'] = directory.name
    output.write_text(json.dumps(report, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wine-source', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.wine_source.is_absolute():
        parser.error('--wine-source must be absolute')
    report, artifacts = audit(args.wine_source)
    if args.output:
        write_report(args.output, report, artifacts)
    else:
        print(json.dumps(report, indent=2))
    if not report['attested']:
        receipt = 'Receipt: '+str(args.output)+'\n' if args.output else ''
        parser.exit(1, '\n'.join(report['errors'])+'\n'+receipt)


if __name__ == '__main__':
    main()
