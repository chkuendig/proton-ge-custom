#!/usr/bin/env bash
# Historical CI control, while the original artifact is retained.
set -euo pipefail
: "${RUNNER_TEMP:?}"
here=$(cd -- "$(dirname -- "$0")" && pwd)
export PYTHONPATH="$here:${PYTHONPATH:-}"
artifact=$(python3 -c 'from inputs import LOCK; print(LOCK["historical_a1"]["artifact"])')
mkdir "$RUNNER_TEMP/a1-download"
if [[ -n ${A1_CONTROL_DIR:-} ]]; then
    cp "$A1_CONTROL_DIR/API.json" "$A1_CONTROL_DIR/control.zip" "$RUNNER_TEMP/a1-download/"
else
    gh api "repos/chkuendig/proton-ge-custom/actions/artifacts/$artifact" > "$RUNNER_TEMP/a1-download/API.json"
    gh api "repos/chkuendig/proton-ge-custom/actions/artifacts/$artifact/zip" > "$RUNNER_TEMP/a1-download/control.zip"
fi
python3 - <<'VERIFY'
import hashlib,json,os,zipfile
from pathlib import Path
from inputs import LOCK
root=Path(os.environ['RUNNER_TEMP'])/'a1-download';pin=LOCK['historical_a1']
api=json.loads((root/'API.json').read_text())
if (api['id']!=pin['artifact'] or (api['expired'] and not os.environ.get('A1_CONTROL_DIR')) or api['workflow_run']['id']!=pin['run'] or
        api['workflow_run']['head_sha']!=pin['proton'] or api['digest']!='sha256:'+pin['zip_sha256']):
    raise ValueError('historical A1 source/artifact differs')
with (root/'control.zip').open('rb') as stream:
    if hashlib.file_digest(stream,'sha256').hexdigest()!=pin['zip_sha256']:
        raise ValueError('historical A1 ZIP checksum mismatch')
with zipfile.ZipFile(root/'control.zip') as archive:
    for name in ['GE-Proton11-7-zluda.tar.gz','MANIFEST','SHA256SUMS']:
        with archive.open(name) as source,(root/name).open('wb') as output:
            import shutil
            shutil.copyfileobj(source,output)
VERIFY
(cd "$RUNNER_TEMP/a1-download" && sha256sum -c SHA256SUMS)
mkdir "$RUNNER_TEMP/a1-tool"
tar -xf "$RUNNER_TEMP/a1-download/GE-Proton11-7-zluda.tar.gz" --strip-components=1 -C "$RUNNER_TEMP/a1-tool"
