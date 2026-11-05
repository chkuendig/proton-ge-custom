#!/usr/bin/env bash
# Invoke only inside the explicitly admitted external SDK, after Proton configure.
set -euo pipefail
: "${PROTON_SOURCE_ROOT:?}" "${PROTON_BUILD_ROOT:?}" "${ZLUDA_PACKAGING_PROFILE:?}"
recipes=$(cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$recipes/packaging/zluda-overlay:$PROTON_SOURCE_ROOT"
python3 "$recipes/packaging/zluda-overlay/inputs.py" bridge-check "$PROTON_SOURCE_ROOT"
if [[ $ZLUDA_PACKAGING_PROFILE == valve-11-0 ]]; then
    exec bash "$recipes/packaging/zluda-overlay/full-valve.sh" "$@"
fi
make -C "$PROTON_BUILD_ROOT" -f Makefile -f "$recipes/make/zluda-wine-bridge.mk" \
    ZLUDA_PACKAGING_ROOT="$recipes" -j3 "$@"
