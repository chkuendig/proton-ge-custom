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
cp target/release/libnvcuda.so "$OUTPUT_DIR/libcuda.so"
cp target/release/libnvcuda.so "$OUTPUT_DIR/libcuda.so.1"

echo ""
echo "=== Building wine_stack_fix.so ==="
gcc -shared -fPIC -o "$OUTPUT_DIR/wine_stack_fix.so" \
    "$PROTON_DIR/patches/rocm/wine_stack_fix.c" -ldl -lpthread
echo "Built wine_stack_fix.so"

echo ""
echo "=== Build complete ==="
echo "Libraries in $OUTPUT_DIR:"
ls -la "$OUTPUT_DIR"/*.so* 2>/dev/null || echo "(no .so files yet — run fetch-rocm.sh first)"
