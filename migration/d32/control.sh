#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$RUNNER_TEMP/d32-control"
gh api repos/chkuendig/proton-ge-custom/actions/artifacts/13340662756 > "$RUNNER_TEMP/d32-control/API.json"
gh api repos/chkuendig/proton-ge-custom/actions/artifacts/13340662756/zip > "$RUNNER_TEMP/d32-control/control.zip"
python3 - <<'PYCONTROL'
import hashlib, json, os, pathlib, zipfile
p=pathlib.Path(os.environ['RUNNER_TEMP'])/'d32-control'
a=json.loads((p/'API.json').read_text())
if a['workflow_run']['id']!=41847768267 or a['workflow_run']['head_sha']!='d32c45244f7f719d6e37c962c52b1cbdc063ce2c': raise ValueError('wrong published control source')
expected='714395e00187e52e8576244b97d89153123d0b24b0949334e0a623521e07961d'
if a['digest']!='sha256:'+expected or hashlib.file_digest((p/'control.zip').open('rb'),'sha256').hexdigest()!=expected: raise ValueError('published control ZIP mismatch')
with zipfile.ZipFile(p/'control.zip') as z:
  for n in ['GE-Proton11-7-zluda.tar.gz','MANIFEST']: (p/n).write_bytes(z.read(n))
PYCONTROL
