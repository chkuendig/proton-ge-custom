#!/usr/bin/env bash
# Hosted SDK only; production source stays at the exact hash-locked patch tree.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DESTDIR:?}" "${NVAPI_TESTS_ROOT:?}"
JOBS=${JOBS:-3}
[[ $JOBS =~ ^[1-3]$ ]]
here=$SOURCE_ROOT/packaging/zluda-overlay
mkdir -p "$BUILD_ROOT" "$DESTDIR" "$NVAPI_TESTS_ROOT"
prepared=$BUILD_ROOT/patched/dxvk-nvapi
if [[ ! -d $prepared ]]; then
    python3 "$here/component_patches.py" prepare "$SOURCE_ROOT" "$prepared" dxvk-nvapi > "$BUILD_ROOT/nvapi-prepare.json"
fi
mkdir -p "$DESTDIR/files/lib/wine/nvapi" "$NVAPI_TESTS_ROOT"
for bits in 64 32; do
    compiler=$([[ $bits == 64 ]] && echo x86_64-w64-mingw32-g++ || echo i686-w64-mingw32-g++)
    "$compiler" -std=c++17 -O2 -static -static-libgcc -static-libstdc++ \
        -I"$prepared/external/Vulkan-Headers/include" -I"$prepared/external/nvapi" \
        "$here/nvapi-provider-probe.cpp" -o "$NVAPI_TESTS_ROOT/provider-$bits.exe" -ldxgi -luuid
done
for spec in '64 x86_64-windows nvapi64 nvapi64-tests' '32 i386-windows nvapi nvapi-tests'; do
    read -r bits arch dll test <<< "$spec"
    production=$BUILD_ROOT/nvapi-production-$bits
    meson setup "$production" "$prepared" --cross-file "$prepared/build-win$bits.txt" --buildtype release -Denable_tests=false
    ninja -C "$production" -j"$JOBS"
    mkdir -p "$DESTDIR/files/lib/wine/nvapi/$arch"
    install -m644 "$production/src/$dll.dll" "$DESTDIR/files/lib/wine/nvapi/$arch/"
    if [[ $bits == 64 ]]; then
        install -m644 "$production/src/nvofapi64.dll" "$DESTDIR/files/lib/wine/nvapi/$arch/"
        test_source=$prepared
    else
        test_source=$BUILD_ROOT/nvapi-pe32-test-source
        python3 "$here/nvapi_package.py" adapt-pe32 "$prepared" "$test_source" "$NVAPI_TESTS_ROOT/PE32-ADAPTER.json"
    fi
    tests=$BUILD_ROOT/nvapi-tests-$bits
    meson setup "$tests" "$test_source" --cross-file "$test_source/build-win$bits.txt" --buildtype release -Denable_tests=true
    ninja -C "$tests" -j"$JOBS" "tests/$test.exe"
    install -m755 "$tests/tests/$test.exe" "$NVAPI_TESTS_ROOT/$test.exe"

done
cc -shared -fPIC -O2 -Wall -Wextra -Werror "$here/nvapi-provider-backend.c" -o "$NVAPI_TESTS_ROOT/provider-backend.so"
python3 "$here/component_patches.py" stage "$SOURCE_ROOT" "$prepared" dxvk-nvapi --dest "$DESTDIR"
python3 "$here/nvapi_package.py" receipt "$SOURCE_ROOT" "$DESTDIR" "$NVAPI_TESTS_ROOT"
