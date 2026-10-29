#!/usr/bin/env bash
# Run only inside the pinned CI SDK image. No host/container launcher here.
set -euo pipefail
: "${BUILD_PART:?}" "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DESTDIR:?}" "${DOWNLOAD_ROOT:?}" "${SDK_ROOT:?}" "${WINE_SDK_ROOT:?}"
export GIT_AUTHOR_NAME='Christian Kündig' GIT_COMMITTER_NAME='Christian Kündig'
export GIT_AUTHOR_EMAIL=christian@kuendig.info GIT_COMMITTER_EMAIL=christian@kuendig.info
here=$SOURCE_ROOT/packaging/zluda-overlay
mkdir -p "$BUILD_ROOT" "$DOWNLOAD_ROOT"
df -h /work
# Fail before the compiler if the hosted runner cannot hold the LLVM/Wine trees.
[[ $(df -B1 --output=avail /work | tail -1) -ge 30000000000 ]]
export PATH="/usr/lib/ccache:/opt/rust/bin:$PATH"
# A GitHub checkout is owned by the runner, while SDK commands run as root.
git config --global --add safe.directory '*'
bash "$here/build-components.sh"
python3 "$here/inputs.py" verify "$DESTDIR"
tar --sort=name --mtime="@$(git -C "$SOURCE_ROOT" show -s --format=%ct HEAD)" \
    --owner=0 --group=0 --numeric-owner -C "$DESTDIR" -cf - . | gzip -n > "/work/$BUILD_PART.tar.gz"
cp "$DESTDIR/MANIFEST" "/work/$BUILD_PART.MANIFEST"
df -h /work
