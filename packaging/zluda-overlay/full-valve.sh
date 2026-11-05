#!/usr/bin/env bash
# Complete Valve source build; run in the admitted external SDK.
set -euo pipefail
: "${PROTON_SOURCE_ROOT:?}" "${PROTON_BUILD_ROOT:?}" "${ZLUDA_PACKAGING_PROFILE:?}"
here=$(cd -- "$(dirname -- "$0")" && pwd)
export SOURCE_ROOT=$PROTON_SOURCE_ROOT
if [[ ${1:-} == --component ]]; then
    export BUILD_PART=${2:?}
    exec bash "$here/build-components.sh"
fi
[[ $# == 0 ]]
export JOBS=3
mkdir -p "$PROTON_BUILD_ROOT/overlays"
for part in zluda bridges; do
    BUILD_PART=$part BUILD_ROOT=$PROTON_BUILD_ROOT/$part \
        DOWNLOAD_ROOT=$PROTON_BUILD_ROOT/downloads SDK_ROOT=$PROTON_BUILD_ROOT/sdk/opt/rocm-7.2.4 \
        WINE_SDK_ROOT=$PROTON_BUILD_ROOT/wine-sdk DESTDIR=$PROTON_BUILD_ROOT/overlay-$part \
        bash "$here/build-components.sh"
    tar -C "$PROTON_BUILD_ROOT/overlay-$part" -cf - . | gzip -n > "$PROTON_BUILD_ROOT/overlays/$part.tar.gz"
done
cp /work/valve-stock.tar.gz /work/VALVE-STOCK.json "$PROTON_BUILD_ROOT/overlays/"
name=$(PYTHONPATH="$here" python3 -c 'from inputs import LOCK; print(LOCK["tool_name"])')
BUILD_ROOT=$PROTON_BUILD_ROOT/assembly DOWNLOAD_ROOT=$PROTON_BUILD_ROOT/downloads \
    DESTDIR=$PROTON_BUILD_ROOT/$name OVERLAYS_ROOT=$PROTON_BUILD_ROOT/overlays OUTPUT_ROOT=$PROTON_BUILD_ROOT/release \
    bash "$here/assemble.sh"
