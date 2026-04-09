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

ROCM_VERSION="${ROCM_VERSION:-6.4.3}"
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

# SteamOS 3.7 repo has ROCm 6.2, SteamOS 3.8 has 6.4
# Select mirror based on requested version
if [[ "$ROCM_VERSION" == 6.2* ]]; then
    ARCH_MIRROR="https://steamdeck-packages.steamos.cloud/archlinux-mirror/extra-3.7/os/x86_64"
else
    ARCH_MIRROR="https://steamdeck-packages.steamos.cloud/archlinux-mirror/extra-3.8/os/x86_64"
fi

# Runtime libs (always needed for bundling)
ARCH_RUNTIME_PKGS=(
    "hip-runtime-amd"
    "hsa-rocr"
    "hsakmt-roct"
    "comgr"
    "rocm-smi-lib"
    "rocprofiler-register"
)

# Optional debugger packages — only fetched when ROCM_DEBUGGER=1.
# rocm-gdb is the AMD GDB fork with AMDGPU wave-level debugging support;
# rocm-dbgapi is the runtime library it talks to. Both go into ext/rocm/.
# On Steam Deck (gfx1033) the kernel must be running with
# HSA_OVERRIDE_GFX_VERSION=10.3.0 in the target process so dbgapi sees
# gfx1030 (which IS in its supported architecture list).
ARCH_DEBUGGER_PKGS=(
    "rocm-dbgapi"
    "rocm-gdb"
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
    if [ "${ROCM_DEBUGGER:-0}" = "1" ]; then
        echo ""
        echo "Downloading ROCm debugger packages..."
        for pkg in "${ARCH_DEBUGGER_PKGS[@]}"; do
            fetch_arch_pkg "$pkg" "opt/rocm/" 2
        done
    fi
else
    echo "Downloading .deb packages from AMD repo..."

    # Auto-discover package filenames from the AMD repo directory listing
    fetch_deb_auto() {
        local pkg_path="$1"
        local url
        # Get the 22.04 package (most compatible)
        url=$(curl -sL "${ROCM_RELEASE}/${pkg_path}/" | grep -o 'href="[^"]*22.04[^"]*\.deb"' | head -1 | sed 's/href="//;s/"//')
        if [ -z "$url" ]; then
            # Fallback: get any amd64 deb
            url=$(curl -sL "${ROCM_RELEASE}/${pkg_path}/" | grep -o 'href="[^"]*amd64\.deb"' | head -1 | sed 's/href="//;s/"//')
        fi
        if [ -n "$url" ]; then
            fetch_deb "${ROCM_RELEASE}/${pkg_path}/${url}"
        else
            echo "  SKIP ${pkg_path} (no .deb found)"
        fi
    }

    # Core runtime libraries
    fetch_deb_auto "h/hip-runtime-amd"    # libamdhip64.so.6
    fetch_deb_auto "h/hsa-rocr"           # libhsa-runtime64.so.1
    fetch_deb_auto "c/comgr"              # libamd_comgr.so.3
    fetch_deb_auto "r/rocm-smi-lib"       # librocm_smi64.so
    fetch_deb_auto "r/rocprofiler-register"  # librocprofiler-register.so

    # Transitive dependencies that may be needed
    # (fmt, glog, gflags are usually available on the system)
fi

echo ""

# Create version compat shim: PhysX 3.3 links against libamdhip64.so.5 but
# ROCm 6.x only ships .so.6. Always overwrite to keep in sync with .so.6.
for dir in "$OUTPUT_DIR/lib" "$OUTPUT_DIR"; do
    if [ -f "$dir/libamdhip64.so.6" ]; then
        cp -f "$dir/libamdhip64.so.6" "$dir/libamdhip64.so.5"
        echo "Created libamdhip64.so.5 (copy of .so.6) in $dir/"
        break
    fi
done

echo ""
echo "Done. Libraries in $OUTPUT_DIR"
if [ "${ROCM_HEADERS:-0}" = "1" ]; then
    echo "Headers mode: $OUTPUT_DIR/include/hip/ should now contain HIP headers"
fi
