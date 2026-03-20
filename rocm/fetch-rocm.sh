#!/bin/bash
# fetch-rocm.sh — Download ROCm runtime libraries for bundling
#
# Downloads ROCm HIP/HSA runtime .so files and headers.
# Supports both Arch/SteamOS (.pkg.tar.zst) and Ubuntu (.deb) packages.
#
# Usage: ./fetch-rocm.sh [output_dir]
# Default output: ../files/lib64/
#
# For ZLUDA build headers (ext/rocm/):
#   ROCM_HEADERS=1 ./fetch-rocm.sh ../../zluda/ext/rocm

set -euo pipefail

ROCM_VERSION="${ROCM_VERSION:-6.2.4}"
OUTPUT_DIR="${1:-$(dirname "$0")/../files/lib64}"

mkdir -p "$OUTPUT_DIR"
WORK_DIR=$(mktemp -d)
trap "rm -rf $WORK_DIR" EXIT

# Detect platform
if command -v pacman &>/dev/null; then
    PLATFORM="arch"
else
    PLATFORM="deb"
fi

# --- Arch/SteamOS: download .pkg.tar.zst from pacman mirror ---

ARCH_MIRROR="https://steamdeck-packages.steamos.cloud/archlinux-mirror/extra-3.7/os/x86_64"

# Runtime libs (always needed for bundling)
ARCH_RUNTIME_PKGS=(
    "hip-runtime-amd"
    "hsa-rocr"
    "hsakmt-roct"
    "comgr"
    "rocm-smi-lib"
    "rocprofiler-register"
)

# Transitive deps (needed for LD_LIBRARY_PATH resolution)
ARCH_DEP_PKGS=(
    "fmt"
    "google-glog"
    "gflags"
)

fetch_arch_pkg() {
    local pkg_name="$1"
    local strip_prefix="$2"  # "opt/rocm/" or "usr/"
    local strip_count="$3"   # 2 or 1
    local url
    url=$(pacman -Sp "$pkg_name" 2>/dev/null | grep "$pkg_name" | head -1)
    if [ -z "$url" ]; then
        echo "  SKIP $pkg_name (not found in repos)"
        return
    fi
    local filename
    filename=$(basename "$url")
    echo "  Fetching $filename..."
    curl -sL "$url" -o "$WORK_DIR/$filename"
    tar xf "$WORK_DIR/$filename" -C "$OUTPUT_DIR" \
        --strip-components="$strip_count" "$strip_prefix" 2>/dev/null || true
    rm -f "$WORK_DIR/$filename"
}

# --- Ubuntu/CI: download .deb from AMD repo ---

ROCM_RELEASE="https://repo.radeon.com/rocm/apt/${ROCM_VERSION}/pool/main"

fetch_deb() {
    local url="$1"
    local pkg_name
    pkg_name=$(basename "$url")
    echo "  Fetching $pkg_name..."
    curl -sL "$url" -o "$WORK_DIR/$pkg_name"
    cd "$WORK_DIR"
    ar x "$pkg_name"
    tar xf data.tar.* -C "$OUTPUT_DIR" --strip-components=4 2>/dev/null || \
    tar xf data.tar.* -C "$OUTPUT_DIR" --strip-components=3 2>/dev/null || true
    rm -f "$WORK_DIR"/{control.tar.*,data.tar.*,debian-binary,"$pkg_name"}
    cd - >/dev/null
}

# --- Main ---

echo "Fetching ROCm ${ROCM_VERSION} runtime libraries (platform: $PLATFORM)"
echo "Output directory: $OUTPUT_DIR"
echo ""

if [ "$PLATFORM" = "arch" ]; then
    echo "Downloading ROCm packages..."
    for pkg in "${ARCH_RUNTIME_PKGS[@]}"; do
        fetch_arch_pkg "$pkg" "opt/rocm/" 2
    done
    echo ""
    echo "Downloading transitive dependencies..."
    for pkg in "${ARCH_DEP_PKGS[@]}"; do
        fetch_arch_pkg "$pkg" "usr/" 1
    done
else
    echo "Ubuntu/CI: download .deb packages from $ROCM_RELEASE"
    echo "Required packages:"
    echo "  hip-runtime-amd (libamdhip64.so.6)"
    echo "  hsa-rocr (libhsa-runtime64.so.1)"
    echo "  comgr (libamd_comgr.so.2)"
    echo "  hsakmt-roct (libhsakmt.so.1)"
    echo ""
    echo "Update URLs below for your ROCm version, then uncomment:"
    echo "  # fetch_deb \$ROCM_RELEASE/h/hip-runtime-amd/hip-runtime-amd_..._amd64.deb"
fi

echo ""

# Create version compat symlink (PhysX 3.3 links against libamdhip64.so.5)
if [ -f "$OUTPUT_DIR/lib/libamdhip64.so.6" ] && [ ! -f "$OUTPUT_DIR/lib/libamdhip64.so.5" ]; then
    cp "$OUTPUT_DIR/lib/libamdhip64.so.6" "$OUTPUT_DIR/lib/libamdhip64.so.5"
    echo "Created libamdhip64.so.5 (copy of .so.6)"
elif [ -f "$OUTPUT_DIR/libamdhip64.so.6" ] && [ ! -f "$OUTPUT_DIR/libamdhip64.so.5" ]; then
    cp "$OUTPUT_DIR/libamdhip64.so.6" "$OUTPUT_DIR/libamdhip64.so.5"
    echo "Created libamdhip64.so.5 (copy of .so.6)"
fi

echo ""
echo "Done. Libraries in $OUTPUT_DIR"
if [ "${ROCM_HEADERS:-0}" = "1" ]; then
    echo "Headers mode: $OUTPUT_DIR/include/hip/ should now contain HIP headers"
fi
