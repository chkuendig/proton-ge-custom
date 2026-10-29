#!/usr/bin/env bash
# CI only: one baseline C build using the exact SDK/epoch of the real payload.
# No runtime/GPU execution, container launcher, installation or prefix changes.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${WINE_SDK_ROOT:?}" "${DESTDIR:?}"
export -n DESTDIR
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$BUILD_ROOT/bridge-equivalence
[[ ! -e $root ]] || { echo 'bridge equivalence directory must be fresh' >&2; exit 1; }
mkdir -p "$root/original" "$root/report"
origin=fa81f481ceffa0b2228e2eacc1530a4c1f9ef494
[[ $(git -C "$SOURCE_ROOT/zluda" rev-parse "$origin^{tree}") == \
   "$(git -C "$SOURCE_ROOT/zluda" rev-parse HEAD:wine/nvcuda)" ]]
git -C "$SOURCE_ROOT/zluda" archive "$origin" | tar -xf - -C "$root/original"
python3 - "$here" "$SOURCE_ROOT" "$WINE_SDK_ROOT" "$root" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from bridge_tools import check_unix_headers
source, sdk, root = map(Path, sys.argv[2:])
check_unix_headers(source, sdk, root / 'report/original-header-probe.json', root / 'original')
PY
BRIDGE_SOURCE=$root/original BUILD_ROOT=$root/build-original BRIDGE_DEST=$root/original-payload \
    bash "$here/build-bridge.sh" 2>&1 | tee "$root/report/original-build.log"
python3 "$here/bridge_equivalence.py" "$root/original-payload" "$DESTDIR/files" \
    "$root/original" "$root/build-original/wow64/exports.def" "$root/report"
mkdir -p "$DESTDIR/provenance"
cp "$root/report/REPORT.json" "$DESTDIR/provenance/BRIDGE-EQUIVALENCE.json"
