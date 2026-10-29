#!/usr/bin/env bash
# Default C bridge, shared by GE overlay CI and the full-source Makefile.
set -euo pipefail
: "${BRIDGE_SOURCE:?}" "${BUILD_ROOT:?}" "${BRIDGE_DEST:?}" "${WINE_SDK_ROOT:?}"
JOBS=${JOBS:-3}
[[ $JOBS =~ ^[1-3]$ ]] || { echo 'JOBS must be 1..3' >&2; exit 2; }
n=$BRIDGE_SOURCE
wl=$BRIDGE_DEST/lib/wine
export PATH="$WINE_SDK_ROOT/bin:$PATH"
mkdir -p "$wl/x86_64-unix" "$wl/x86_64-windows" "$wl/i386-windows"
meson setup "$BUILD_ROOT/nvcuda-x64" "$n" --cross-file "$n/build-wine64.txt" \
    --cross-file "$WINE_SDK_ROOT/nvcuda-wine64.ini" \
    --buildtype release -Dfakedll=true
ninja -C "$BUILD_ROOT/nvcuda-x64" -j"$JOBS"
install -m755 "$BUILD_ROOT/nvcuda-x64/dlls/nvcuda/nvcuda.dll.so" "$wl/x86_64-unix/"
install -m644 "$BUILD_ROOT/nvcuda-x64/dlls/nvcuda_fdll/nvcuda.dll" "$wl/x86_64-windows/"
# Same compiler flags and generator as the imported tests/build-wow64.sh.
wow=$BUILD_ROOT/wow64
mkdir -p "$wow"
python3 "$n/tests/generate-wow64-unsupported.py" "$n" "$wow"
i686-w64-mingw32-gcc -O2 -Wall -Wextra -Werror -shared \
    "$n/dlls/nvcuda/wow64/pe.c" "$wow/unsupported.c" "$wow/exports.def" \
    -Wl,--kill-at -o "$wl/i386-windows/nvcuda.dll"
"$WINE_SDK_ROOT/bin/winebuild" --builtin "$wl/i386-windows/nvcuda.dll"
cc -O2 -Wall -Wextra -Werror -fPIC -shared "$n/dlls/nvcuda/wow64/unix.c" \
    -ldl -pthread -o "$wl/x86_64-unix/nvcuda.so"
