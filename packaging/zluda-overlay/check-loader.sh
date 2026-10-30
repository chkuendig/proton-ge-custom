#!/usr/bin/env bash
# CPU-only check in the pinned SDK. GPU and pressure-vessel qualification remain
# separate; the SDK's wider library set is not proof of target availability.
set -euo pipefail
: "${DESTDIR:?}"
export LC_ALL=C
export LD_LIBRARY_PATH="$DESTDIR/files/lib64/wine/x86_64-unix:$DESTDIR/files/lib64:$DESTDIR/files/lib/x86_64-linux-gnu:$DESTDIR/files/lib/wine/x86_64-unix"
# Every newly supplied ELF, including all seven ROCm libraries and libnuma.
mapfile -d '' -t files < <(find "$DESTDIR/files/lib64" "$DESTDIR/files/lib/wine/x86_64-unix" "$DESTDIR/files/lib/wine-cuda/zluda/x86_64-unix" -maxdepth 1 -type f -print0)
for file in "${files[@]}"; do
    [[ $(head -c4 "$file") == $'\x7fELF' ]] || continue
    echo "== $file"
    if ! readelf -d "$file" | grep -q '(NEEDED)'; then
        echo "no dynamic dependencies"
        continue
    fi
    output=$(ldd "$file")
    printf '%s\n' "$output"
    if grep -q 'not found' <<< "$output"; then
        echo "unresolved dependencies: $file" >&2
        exit 1
    fi
    if [[ $file == "$DESTDIR/files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so" ]]; then
        python3 "$(dirname -- "$0")/wine_provider.py" --tool "$DESTDIR" <<< "$output"
    fi
done
