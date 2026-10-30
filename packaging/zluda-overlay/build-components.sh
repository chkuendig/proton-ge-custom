#!/usr/bin/env bash
# Public overlay builder; no Steam, Deck, distrobox or installation side effects.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${SDK_ROOT:?}" "${DESTDIR:?}" "${WINE_SDK_ROOT:?}" "${BUILD_ROOT:?}" "${DOWNLOAD_ROOT:?}"
JOBS=${JOBS:-3}
[[ $JOBS =~ ^[1-3]$ ]] || { echo 'JOBS must be 1..3' >&2; exit 2; }
export JOBS
part=${BUILD_PART:-all}
[[ $part == all || $part == zluda || $part == bridges ]]
here=$(cd -- "$(dirname -- "$0")" && pwd)
python3 "$here/inputs.py" check "$SOURCE_ROOT" --part "$part"
[[ ! -e $DESTDIR ]] || { echo "DESTDIR already exists: $DESTDIR" >&2; exit 1; }
mkdir -p "$DESTDIR" "$BUILD_ROOT" "$DOWNLOAD_ROOT"
# Keep the final overlay destination out of upstream build/install environments
# (including cargo's CMake builds). Only our assembly helpers consume it.
export -n DESTDIR
export SOURCE_DATE_EPOCH
SOURCE_DATE_EPOCH=$(git -C "$SOURCE_ROOT" show -s --format=%ct HEAD)
wl=$DESTDIR/files/lib/wine
lib64=$DESTDIR/files/lib64
mkdir -p "$wl/x86_64-unix" "$wl/x86_64-windows" "$wl/i386-windows" "$lib64"

if [[ $part == zluda || $part == all ]]; then
    python3 "$here/inputs.py" fetch "$DOWNLOAD_ROOT" --group zluda
    # Extraction and per-package license collection are checked before compiling.
    DESTDIR=$DESTDIR python3 "$here/overlay.py" sdk
    BUILD_PART=zluda bash "$here/install-rust.sh"
    export PATH="$BUILD_ROOT/rust/bin:$PATH"
    [[ $(rustc --version) == 'rustc 1.99.0 '* ]]
    export CARGO_HOME=${CARGO_HOME:-$BUILD_ROOT/cargo}
    export CARGO_BUILD_JOBS=$JOBS CMAKE_BUILD_PARALLEL_LEVEL=$JOBS
    export ZLUDA_HIP_SDK_ROOT=$SDK_ROOT
    export ZLUDA_MATHCOMPAT_OBJECT=$BUILD_ROOT/mathcompat.o
    cc -O2 -fPIC -fno-builtin -c "$here/steamos-math-compat.c" -o "$ZLUDA_MATHCOMPAT_OBJECT"
    mkdir -p "$BUILD_ROOT/stubs"
    for name in rocsparse MIOpen hipblaslt rocblas; do
        printf '\n' | cc -shared -x c - -o "$BUILD_ROOT/stubs/lib$name.so"
    done
    export LIBRARY_PATH="$BUILD_ROOT/stubs:$SDK_ROOT/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
    export LD_LIBRARY_PATH="$SDK_ROOT/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    # cmake-rs's compiler launcher accepts ccache as well as sccache. Cache key
    # includes LLVM, SDK digest, Rust, and this build configuration in CI.
    export SCCACHE_PATH
    SCCACHE_PATH=$(command -v ccache)
    ccache --max-size=4G
    bash "$here/build-native.sh"
    cp "$BUILD_ROOT/native/link-layout.json" "$DESTDIR/provenance/NATIVE-LINK-LAYOUT.json"
    objcopy --strip-debug "$SOURCE_ROOT/zluda/target/release/libnvcuda.so" "$lib64/libcuda.so.1"
    ln -s libcuda.so.1 "$lib64/libcuda.so"
    # Keep the launcher LD order untouched. Both Wine paths resolve to GE's
    # canonical files/lib/wine tree; lib64 still wins for CUDA and ROCm.
    ln -s ../lib/wine "$lib64/wine"
    tar -xf "$DOWNLOAD_ROOT/numactl-2.0.18.tar.gz" -C "$BUILD_ROOT"
    (
        cd "$BUILD_ROOT/numactl-2.0.18"
        ./autogen.sh
        ./configure --prefix=/usr --libdir=/usr/lib --disable-static
        make -j"$JOBS" libnuma.la
        objcopy --strip-debug .libs/libnuma.so.1.0.0 "$lib64/libnuma.so.1"
    )
    mkdir -p "$DESTDIR/sources"
    cp "$DOWNLOAD_ROOT/numactl-2.0.18.tar.gz" "$DESTDIR/sources/"
fi

if [[ $part == bridges || $part == all ]]; then
    mkdir -p "$BUILD_ROOT/log"
    # Admit and patch before Wine's expensive prerequisites; original gitlinks
    # remain clean upstream checkouts throughout the CI build.
    python3 "$here/component_patches.py" prepare "$SOURCE_ROOT" \
        "$BUILD_ROOT/patched/dxvk-nvapi" dxvk-nvapi > "$BUILD_ROOT/log/dxvk-nvapi-patches.json"
    python3 "$here/inputs.py" fetch "$DOWNLOAD_ROOT" --group bridges
    BUILD_PART=bridges bash "$here/install-rust.sh"
    export PATH="$BUILD_ROOT/rust/bin:$PATH"
    export CARGO_HOME=${CARGO_HOME:-$BUILD_ROOT/cargo}
    # This source line changes ntdll relative to GE11-7. Build exactly the
    # changed Wine modules and retain the existing ELF dependency/version gate.
    bash "$here/build-wine.sh"
    export PATH="$WINE_SDK_ROOT/bin:$PATH"
    python3 "$here/bridge_tools.py" "$SOURCE_ROOT" "$BUILD_ROOT/wine/log/bridge-tools-sdk.json" --sdk "$WINE_SDK_ROOT" --generated
    n=$SOURCE_ROOT/zluda/wine/zluda_wine
    BRIDGE_SOURCE=$n BRIDGE_DEST=$DESTDIR bash "$here/build-bridge.sh"
    for spec in '64 x86_64-windows' '32 i386-windows'; do
        read -r bits arch <<< "$spec"
        a=$BUILD_ROOT/patched/dxvk-nvapi
        ab=$BUILD_ROOT/nvapi-$bits
        meson setup "$ab" "$a" --cross-file "$a/build-win$bits.txt" --buildtype release -Denable_tests=false
        ninja -C "$ab" -j"$JOBS"
        mkdir -p "$wl/nvapi/$arch"
        if [[ $bits == 64 ]]; then
            install -m644 "$ab/src/nvapi64.dll" "$ab/src/nvofapi64.dll" "$wl/nvapi/$arch/"
        else
            install -m644 "$ab/src/nvapi.dll" "$wl/nvapi/$arch/"
        fi
        d=$SOURCE_ROOT/dxvk
        db=$BUILD_ROOT/dxvk-$bits
        meson setup "$db" "$d" --cross-file "$d/build-win$bits.txt" --buildtype release -Dbuild_id=false
        ninja -C "$db" -j"$JOBS"
        mkdir -p "$wl/dxvk/$arch"
        for name in d3d11 dxgi d3d10core d3d9 d3d8; do
            mapfile -t files < <(find "$db/src" -name "$name.dll" -type f)
            [[ ${#files[@]} == 1 ]] || { echo "missing/ambiguous $name.dll" >&2; exit 1; }
            install -m644 "${files[0]}" "$wl/dxvk/$arch/"
        done
    done
    (cd "$BUILD_ROOT/wine/overlay" && sha256sum -c FILES.sha256)
    cp -a "$BUILD_ROOT/wine/overlay/files/." "$DESTDIR/files/"
    mkdir -p "$DESTDIR/provenance" "$DESTDIR/sources"
    cp "$BUILD_ROOT/wine/overlay/OVERLAY" "$DESTDIR/provenance/WINE-OVERLAY"
    cp "$BUILD_ROOT/wine/overlay/ELF-DEPENDENCIES" "$DESTDIR/provenance/WINE-ELF-DEPENDENCIES"
    cp "$BUILD_ROOT/wine/wine-scope.json" "$DESTDIR/provenance/WINE-SCOPE.json"
    cp "$BUILD_ROOT/wine/proton-wine.mk" "$DESTDIR/provenance/WINE-BUILD-RECIPE.mk"
    git -C "$BUILD_ROOT/wine/src/wine" archive HEAD | gzip -n > "$DESTDIR/sources/wine-prepared.tar.gz"
    python3 "$here/source_archive.py" --source "$SOURCE_ROOT/zluda" \
        --build "$BUILD_ROOT/zluda-wine/target" --output "$DESTDIR/sources/zluda-wine.tar.gz"
    python3 "$here/component_patches.py" stage "$SOURCE_ROOT" \
        "$BUILD_ROOT/patched/dxvk-nvapi" dxvk-nvapi --dest "$DESTDIR"
fi
DESTDIR=$DESTDIR python3 "$here/overlay.py" metadata --part "$part"
python3 "$here/inputs.py" manifest "$DESTDIR"
python3 "$here/inputs.py" verify "$DESTDIR"
echo "Complete $part overlay: $DESTDIR"
