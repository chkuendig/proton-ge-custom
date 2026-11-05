#!/usr/bin/env bash
set -euo pipefail
: "${1:?source offer output directory}"
root=$(git rev-parse --show-toplevel)
out=$(realpath -m -- "$1")
mkdir -p "$(dirname "$out")"
mkdir "$out"
cd "$root"
test -z "$(git status --porcelain --untracked-files=no)"
git bundle create "$out/packaging.bundle" HEAD
git bundle verify "$out/packaging.bundle"
git cat-file --batch-all-objects --batch-check > "$out/FETCHED-OBJECTS.txt"
git ls-files --stage > "$out/TRACKED-PATHS.txt"
PYTHONPATH="$root/packaging/zluda-overlay" python3 - "$out" <<'PYRECEIPT'
import json, pathlib, sys
from inputs import PROFILE, check_profile_source, git, packaging_identity, sha256
out = pathlib.Path(sys.argv[1])
check_profile_source()
receipt = {'packaging': packaging_identity(), 'tree': git('.', 'rev-parse', 'HEAD^{tree}'),
           'proton': PROFILE['source_commit'], 'profile': PROFILE['base'], 'source_path': PROFILE['source_path'],
           'files': {p.name: sha256(p) for p in out.iterdir() if p.is_file()}}
(out / 'SOURCE-OFFER.json').write_text(json.dumps(receipt, indent=2) + '\n')
PYRECEIPT
cat > "$out/REBUILD.txt" <<'REBUILD'
From the downloaded source-offer directory:
  sha256sum -c SHA256SUMS
  git clone packaging.bundle recipes
  cd recipes
  git remote set-url origin https://github.com/chkuendig/proton-ge-custom
  python3 - ../SOURCE-OFFER.json <<'VERIFY'
import json, subprocess, sys
receipt = json.load(open(sys.argv[1]))
assert receipt['packaging']['repository'] == 'https://github.com/chkuendig/proton-ge-custom'
for query, expected in [('HEAD', receipt['packaging']['commit']), ('HEAD^{tree}', receipt['tree'])]:
    if subprocess.check_output(['git', 'rev-parse', query], text=True).strip() != expected:
        raise SystemExit('source-offer commit/tree mismatch')
VERIFY

Alternatively, clone https://github.com/chkuendig/proton-ge-custom directly and
check out packaging.commit from SOURCE-OFFER.json; verify its tree as above.
Initialize SOURCE-OFFER.json source_path and its recursive public submodules with Git LFS.
Select ZLUDA_PACKAGING_PROFILE=ge-11-7 or valve-11-0 for the recorded profile and use .github/workflows/zluda-overlay.yml.
The complete Git bundle includes migration/d32/baseline.sh; the smaller shipped
recipe capsules require that helper from this bundle. Component sources, licenses
and notices are also included in the tool's sources/ and licenses/ directories.
The manifest records the actual public executor commit; hub subtree equality is
checked separately during hub promotion. No private hub checkout or hub credential is used by CI.
The migration release-audit control expires on 2026-11-18; build and CPU jobs
perform the rebuild. Retire that migration gate before relying on a later audit.
REBUILD
(cd "$out" && sha256sum packaging.bundle SOURCE-OFFER.json REBUILD.txt FETCHED-OBJECTS.txt TRACKED-PATHS.txt > SHA256SUMS)
