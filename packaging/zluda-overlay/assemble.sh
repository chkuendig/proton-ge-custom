#!/usr/bin/env bash
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DESTDIR:?}" "${DOWNLOAD_ROOT:?}" "${OVERLAYS_ROOT:?}" "${OUTPUT_ROOT:?}"
here=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ $ZLUDA_PACKAGING_PROFILE == valve-11-0 ]]; then group=assembly; else group=ge; fi
python3 "$here/inputs.py" fetch "$DOWNLOAD_ROOT" --group "$group"
python3 "$here/overlay.py" assemble --overlays "$OVERLAYS_ROOT"
mkdir -p "$OUTPUT_ROOT"
name=$(PYTHONPATH="$here" python3 -c 'from inputs import LOCK; print(LOCK["tool_name"])')
[[ $(basename "$DESTDIR") == "$name" ]]
cp "$DESTDIR/MANIFEST" "$OUTPUT_ROOT/MANIFEST"
# Preserve older GE mtimes: Wine uses wine.inf mtime to decide prefix updates.
times=()
if [[ $ZLUDA_PACKAGING_PROFILE != valve-11-0 ]]; then
    times=(--clamp-mtime --mtime="@$(PYTHONPATH="$here" python3 -c 'from inputs import SOURCE_EPOCH; print(SOURCE_EPOCH)')")
fi
tar --sort=name "${times[@]}" \
    --owner=0 --group=0 --numeric-owner -C "$(dirname "$DESTDIR")" -cf - "$name" \
    | gzip -n > "$OUTPUT_ROOT/$name.tar.gz"
(cd "$OUTPUT_ROOT" && sha256sum "$name.tar.gz" MANIFEST > SHA256SUMS)
stat -c 'compressed_archive_bytes=%s' "$OUTPUT_ROOT/$name.tar.gz" > "$OUTPUT_ROOT/SIZES.txt"
cat "$DESTDIR/provenance/SIZES.json" >> "$OUTPUT_ROOT/SIZES.txt"
