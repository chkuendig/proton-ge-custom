#!/usr/bin/env bash
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DESTDIR:?}" "${DOWNLOAD_ROOT:?}" "${OVERLAYS_ROOT:?}" "${OUTPUT_ROOT:?}"
here=$SOURCE_ROOT/packaging/zluda-overlay
python3 "$here/inputs.py" fetch "$DOWNLOAD_ROOT" --group ge
python3 "$here/overlay.py" assemble --overlays "$OVERLAYS_ROOT"
mkdir -p "$OUTPUT_ROOT"
name=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["tool_name"])' "$here/inputs.lock.json")
[[ $(basename "$DESTDIR") == "$name" ]]
cp "$DESTDIR/MANIFEST" "$OUTPUT_ROOT/MANIFEST"
# --clamp-mtime keeps GE's own (older) mtimes. Wine compares the prefix's
# .update-timestamp with wine.inf's mtime; a per-build mtime makes every
# tool switch rerun Wine's prefix update, whose fake-dll install writes
# through prefix links into the tool that created them.
tar --sort=name --mtime="@$(git -C "$SOURCE_ROOT" show -s --format=%ct HEAD)" --clamp-mtime \
    --owner=0 --group=0 --numeric-owner -C "$(dirname "$DESTDIR")" -cf - "$name" \
    | gzip -n > "$OUTPUT_ROOT/$name.tar.gz"
(cd "$OUTPUT_ROOT" && sha256sum "$name.tar.gz" MANIFEST > SHA256SUMS)
stat -c 'compressed_archive_bytes=%s' "$OUTPUT_ROOT/$name.tar.gz" > "$OUTPUT_ROOT/SIZES.txt"
cat "$DESTDIR/provenance/SIZES.json" >> "$OUTPUT_ROOT/SIZES.txt"
