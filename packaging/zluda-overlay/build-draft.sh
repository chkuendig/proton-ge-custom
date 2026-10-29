#!/usr/bin/env bash
# Hosted compile validation only; never creates an installable tool/overlay.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DOWNLOAD_ROOT:?}" "${WINE_SDK_ROOT:?}"
JOBS=${JOBS:-3}
[[ $JOBS =~ ^[1-3]$ ]] || { echo 'JOBS must be 1..3' >&2; exit 2; }
export JOBS
export -n DESTDIR
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$SOURCE_ROOT" show -s --format=%ct HEAD)
here=$(cd -- "$(dirname -- "$0")" && pwd)
python3 "$here/inputs.py" check "$SOURCE_ROOT" --part bridges
python3 "$here/inputs.py" fetch "$DOWNLOAD_ROOT" --group bridges
BUILD_PART=bridges bash "$here/install-rust.sh"
export PATH="$BUILD_ROOT/rust/bin:$PATH"
export CARGO_HOME=${CARGO_HOME:-$BUILD_ROOT/cargo}
bash "$here/build-wine.sh"
export PATH="$WINE_SDK_ROOT/bin:$PATH"
python3 "$here/bridge_tools.py" "$SOURCE_ROOT" "$BUILD_ROOT/wine/log/bridge-tools-sdk.json" --sdk "$WINE_SDK_ROOT" --generated
export PILOT_BUILD_ROOT=$BUILD_ROOT/zluda-wine-draft
mkdir -p "$BUILD_ROOT/log"
bridge=$SOURCE_ROOT/zluda/wine/zluda_wine
bash "$bridge/build-pilot.sh" 2>&1 | tee "$BUILD_ROOT/log/zluda-wine-draft.log"
commit=$(git -C "$bridge" rev-parse HEAD)
python3 "$here/cuda_package.py" draft "$PILOT_BUILD_ROOT/artifacts" --commit "$commit"
echo 'X64 compile draft only: no PE32/WoW64 parity, overlay or installable tool.'
