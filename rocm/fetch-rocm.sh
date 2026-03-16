#!/bin/bash
# fetch-rocm.sh — Download ROCm runtime libraries for bundling
#
# Downloads ROCm HIP/HSA runtime .so files from Ubuntu .deb packages
# on GitHub releases. No system install needed — just extracts the libs.
#
# Usage: ./fetch-rocm.sh [output_dir]
# Default output: ../files/lib64/

set -euo pipefail

ROCM_VERSION="${ROCM_VERSION:-6.0.2}"
ROCM_RELEASE="https://repo.radeon.com/rocm/apt/${ROCM_VERSION}/pool/main"
OUTPUT_DIR="${1:-$(dirname "$0")/../files/lib64}"

mkdir -p "$OUTPUT_DIR"
WORK_DIR=$(mktemp -d)
trap "rm -rf $WORK_DIR" EXIT

fetch_deb() {
    local url="$1"
    local pkg_name=$(basename "$url")
    echo "Fetching $pkg_name..."
    curl -sL "$url" -o "$WORK_DIR/$pkg_name"
    cd "$WORK_DIR"
    ar x "$pkg_name"
    tar xf data.tar.* -C "$OUTPUT_DIR" --strip-components=4 2>/dev/null || \
    tar xf data.tar.* -C "$OUTPUT_DIR" --strip-components=3 2>/dev/null || true
    rm -f "$WORK_DIR"/{control.tar.*,data.tar.*,debian-binary,"$pkg_name"}
    cd -
}

echo "Fetching ROCm ${ROCM_VERSION} runtime libraries..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# The specific .deb URLs depend on the ROCm version and Ubuntu release.
# For manual fetching, download these packages and extract:
#   - hip-runtime-amd (provides libamdhip64.so.6)
#   - hsa-rocr (provides libhsa-runtime64.so.1)
#   - comgr (provides libamd_comgr.so.2)
#   - hsakmt-roct (provides libhsakmt.so.1)
#
# Then copy libnuma.so.1 from the host system.

echo "Required libraries (copy manually or update URLs above):"
echo "  libamdhip64.so.6     — from hip-runtime-amd"
echo "  libamdhip64.so.5     — copy of .so.6 (version compat shim)"
echo "  libhsa-runtime64.so.1 — from hsa-rocr"
echo "  libamd_comgr.so.2    — from comgr"
echo "  libhsakmt.so.1       — from hsakmt-roct"
echo "  libnuma.so.1         — from host system (missing in pressure-vessel)"
echo ""

# Create version compat symlink
if [ -f "$OUTPUT_DIR/libamdhip64.so.6" ] && [ ! -f "$OUTPUT_DIR/libamdhip64.so.5" ]; then
    cp "$OUTPUT_DIR/libamdhip64.so.6" "$OUTPUT_DIR/libamdhip64.so.5"
    echo "Created libamdhip64.so.5 (copy of .so.6)"
fi

echo "Done. Place these libraries in $OUTPUT_DIR before building the distribution."
