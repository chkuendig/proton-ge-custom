#!/bin/bash
# build-zluda.sh — Build ZLUDA and wine_stack_fix.so
#
# Prerequisites:
#   - Rust toolchain (rustup)
#   - System GCC (NOT ROCm clang — causes logf128 type mismatch)
#   - ROCm HIP headers + libamdhip64.so (for linking)
#   - CMake + Ninja
#   - Git LFS (for .bc bitcode files)
#
# Usage: ./build-zluda.sh [zluda_source_dir]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROTON_DIR="$(dirname "$SCRIPT_DIR")"
ZLUDA_DIR="${1:-$PROTON_DIR/zluda}"
OUTPUT_DIR="$PROTON_DIR/files/lib64"
WINE_UNIX_DIR="$OUTPUT_DIR/wine/x86_64-unix"
WINE_WIN_DIR="$OUTPUT_DIR/wine/x86_64-windows"
NVIDIA_LIBS_DIR="$PROTON_DIR/nvidia-libs"

if [ ! -d "$ZLUDA_DIR" ]; then
    echo "Error: ZLUDA source not found at $ZLUDA_DIR"
    echo "Initialize submodule: git submodule update --init zluda"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "=== Building ZLUDA ==="
echo "Source: $ZLUDA_DIR"
echo "Output: $OUTPUT_DIR"
echo ""

# Ensure git LFS files are pulled
cd "$ZLUDA_DIR"
if command -v git-lfs &>/dev/null; then
    git lfs pull
else
    echo "WARNING: git-lfs not found. .bc files may be LFS pointers!"
    echo "Install git-lfs and run 'git lfs pull' in $ZLUDA_DIR"
fi

# Verify LLVM submodule
if [ ! -f "ext/llvm-project/llvm/CMakeLists.txt" ]; then
    echo "LLVM submodule not initialized. Running git submodule update..."
    git submodule update --init ext/llvm-project
fi

# Build ZLUDA (uses system GCC via cargo xtask)
echo "Building ZLUDA with cargo xtask --release..."
LIBRARY_PATH="${LIBRARY_PATH:-}:/opt/rocm/lib" cargo xtask --release

# Copy ZLUDA output
echo "Copying ZLUDA libraries..."
mkdir -p "$WINE_UNIX_DIR" "$WINE_WIN_DIR"
cp target/release/libnvcuda.so "$OUTPUT_DIR/libcuda.so"
cp target/release/libnvcuda.so "$OUTPUT_DIR/libcuda.so.1"
cp target/release/libnvcuda.so "$WINE_UNIX_DIR/libcuda.so"
cp target/release/libnvcuda.so "$WINE_UNIX_DIR/libcuda.so.1"
echo "  -> lib64/libcuda.so{,.1}"
echo "  -> wine/x86_64-unix/libcuda.so{,.1}"

echo ""
echo "=== Building wine_stack_fix.so ==="
gcc -shared -fPIC -o "$OUTPUT_DIR/wine_stack_fix.so" \
    "$PROTON_DIR/patches/rocm/wine_stack_fix.c" -ldl -lpthread
echo "Built wine_stack_fix.so"

echo ""
echo "=== Installing nvcuda relay (SveSop/nvidia-libs) ==="
NVCUDA_SRC="$NVIDIA_LIBS_DIR/nvcuda"
if [ -d "$NVCUDA_SRC" ]; then
    # Build nvcuda.dll.so (Unix relay) and nvcuda.dll (Wine PE stub) from source
    if command -v winebuild &>/dev/null && command -v meson &>/dev/null; then
        echo "Building nvcuda from source..."
        cd "$NVCUDA_SRC"
        if [ ! -d "builddir" ]; then
            meson setup builddir --cross-file build-wine64.txt \
                --prefix="$OUTPUT_DIR" -Dfakedll=true
        fi
        cd builddir && ninja
        # Install: fakedll mode puts files in lib/wine/{x86_64-unix,x86_64-windows}/
        DESTDIR="" ninja install
        echo "Built and installed nvcuda relay from source"
    else
        echo "winebuild/meson not available, checking for prebuilt nvcuda..."
        # Fall back to prebuilt from nvidia-libs release
        if [ -f "$NVIDIA_LIBS_DIR/x64/nvcuda.dll" ]; then
            cp "$NVIDIA_LIBS_DIR/x64/nvcuda.dll" "$WINE_WIN_DIR/nvcuda.dll"
            echo "  -> wine/x86_64-windows/nvcuda.dll (prebuilt)"
        fi
        if [ -f "$WINE_UNIX_DIR/nvcuda.dll.so" ]; then
            echo "  nvcuda.dll.so already deployed (keeping existing)"
        else
            echo "  WARNING: nvcuda.dll.so not found in $WINE_UNIX_DIR"
            echo "  Download from: https://github.com/SveSop/nvidia-libs/releases"
            echo "  Copy x64/wine/x86_64-unix/nvcuda.dll.so to $WINE_UNIX_DIR/"
        fi
    fi
else
    echo "nvidia-libs submodule not found at $NVIDIA_LIBS_DIR"
    echo "Run: git submodule update --init nvidia-libs"
fi

echo ""
echo "=== Build complete ==="
echo ""
echo "Deployment checklist:"
echo "  lib64/libcuda.so{,.1}                  — ZLUDA"
echo "  wine/x86_64-unix/libcuda.so{,.1}       — ZLUDA (Wine dlopen path)"
echo "  wine/x86_64-unix/nvcuda.dll.so          — SveSop relay (CUDA -> ZLUDA)"
echo "  wine/x86_64-windows/nvcuda.dll           — Wine PE stub"
echo "  lib64/wine_stack_fix.so                  — ROCm stack fix"
echo "  lib64/libamdhip64.so.{5,6}              — ROCm HIP (from fetch-rocm.sh)"
echo ""
ls -la "$OUTPUT_DIR"/libcuda.so* "$WINE_UNIX_DIR"/nvcuda.dll.so "$WINE_UNIX_DIR"/libcuda.so* "$WINE_WIN_DIR"/nvcuda.dll 2>/dev/null || true
