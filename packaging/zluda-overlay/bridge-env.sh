#!/usr/bin/env bash
# Source this only after the reviewer has produced the matching SDK receipt.
set -euo pipefail
: "${BRIDGE_SOURCE:?}" "${WINE_SDK_ROOT:?}"
export ZLUDA_WINE_SOURCE_ID
ZLUDA_WINE_SOURCE_ID=$(git -C "$BRIDGE_SOURCE" rev-parse HEAD)
export SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$BRIDGE_SOURCE" show -s --format=%ct HEAD)}
export ZLUDA_WINE_ABI_RECEIPT=$WINE_SDK_ROOT/ABI-RECEIPT.json
export ZLUDA_WINE_ABI_RECEIPT_SHA256
ZLUDA_WINE_ABI_RECEIPT_SHA256=$(sha256sum "$ZLUDA_WINE_ABI_RECEIPT" | cut -d' ' -f1)
unset WINE_SOURCE_ROOT ZLUDA_WINE_WINE_INCLUDE ZLUDA_WINE_WINE_GENERATED
if [[ $# -gt 0 ]]; then exec "$@"; fi
