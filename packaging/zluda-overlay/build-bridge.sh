#!/usr/bin/env bash
# Generated builtin PE + Unixlib only. Never substitute the Stage A C relay.
set -euo pipefail
: "${BRIDGE_SOURCE:?}" "${BUILD_ROOT:?}" "${BRIDGE_DEST:?}" "${WINE_SDK_ROOT:?}"
JOBS=${JOBS:-3}
[[ $JOBS =~ ^[1-3]$ ]] || { echo 'JOBS must be 1..3' >&2; exit 2; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
export PATH="$WINE_SDK_ROOT/bin:$PATH"
export ZLUDA_WINE_BUILD_ROOT=$BUILD_ROOT/zluda-wine
source "$here/bridge-env.sh"
mkdir -p "$BUILD_ROOT/log"
# Full checkout provenance, pinned independent crate tree and lock are checked
# by inputs.py before this entry in both CI and make redist.
commit=$(git -C "$BRIDGE_SOURCE" rev-parse HEAD)
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["components"]["zluda"])' "$here/inputs.lock.json")
[[ $commit == "$expected" ]] || { echo 'ZLUDA Wine source pin mismatch' >&2; exit 1; }
# All three targets and the independent lock are required by this entry. Its
# incomplete source must fail rather than package a pilot or a C fallback.
bash "$BRIDGE_SOURCE/build-bridge.sh" 2>&1 | tee "$BUILD_ROOT/log/zluda-wine.log"
python3 "$here/cuda_package.py" install "$BRIDGE_DEST" \
    --artifacts "$ZLUDA_WINE_BUILD_ROOT/artifacts" --commit "$commit"
# This source contract attests the actual prepared Wine callback ABI. Keep it
# beside the installed module provenance and in failure logs.
install -m644 "$ZLUDA_WINE_BUILD_ROOT/WINE-CALLBACK-ATTESTATION.json" \
    "$BRIDGE_DEST/provenance/WINE-CALLBACK-ATTESTATION.json"
