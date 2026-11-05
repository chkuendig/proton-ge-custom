#!/usr/bin/env bash
# Run only inside the pinned CI SDK image. No host/container launcher here.
set -euo pipefail
: "${BUILD_PART:?}" "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DESTDIR:?}" "${DOWNLOAD_ROOT:?}" "${SDK_ROOT:?}" "${WINE_SDK_ROOT:?}"
export GIT_AUTHOR_NAME='Christian Kündig' GIT_COMMITTER_NAME='Christian Kündig'
export GIT_AUTHOR_EMAIL=christian@kuendig.info GIT_COMMITTER_EMAIL=christian@kuendig.info
here=$(cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$BUILD_ROOT" "$DOWNLOAD_ROOT"
df -h /work
# Fail before the compiler if the hosted runner cannot hold the LLVM/Wine trees.
minimum=$(PYTHONPATH="$here" python3 -c 'import os; from inputs import PROFILE; print(PROFILE.get("minimum_build_bytes", {}).get(os.environ["BUILD_PART"], 30000000000))')
available=$(df -B1 --output=avail /work | tail -1)
if (( available < minimum )); then
    printf 'Insufficient build space: %s bytes free; need %s\n' "$available" "$minimum" >&2
    exit 2
fi
export PATH="/usr/lib/ccache:/opt/rust/bin:$PATH"
# A GitHub checkout is owned by the runner, while SDK commands run as root.
git config --global --add safe.directory '*'
if [[ $ZLUDA_PACKAGING_PROFILE == valve-11-0 ]]; then
    PROTON_SOURCE_ROOT=$SOURCE_ROOT PROTON_BUILD_ROOT=$BUILD_ROOT bash "$here/../../make/full-source.sh" --component "$BUILD_PART"
else
    bash "$here/build-components.sh"
fi
python3 "$here/inputs.py" verify "$DESTDIR"
tar --sort=name --mtime="@$(PYTHONPATH="$here" python3 -c 'from inputs import SOURCE_EPOCH; print(SOURCE_EPOCH)')" \
    --owner=0 --group=0 --numeric-owner -C "$DESTDIR" -cf - . | gzip -n > "/work/$BUILD_PART.tar.gz"
cp "$DESTDIR/MANIFEST" "/work/$BUILD_PART.MANIFEST"
df -h /work
