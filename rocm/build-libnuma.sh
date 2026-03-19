#!/bin/bash
# build-libnuma.sh — Fetch and build libnuma from source
#
# libnuma.so.1 is required by ROCm's HSA runtime but is missing from
# Steam's pressure-vessel container. This script downloads the numactl
# source and builds a minimal libnuma.so.1 for bundling.
#
# Usage: ./build-libnuma.sh [output_dir]
# Default output: ../files/lib64/

set -euo pipefail

NUMA_VERSION="${NUMA_VERSION:-2.0.18}"
NUMA_URL="https://github.com/numactl/numactl/releases/download/v${NUMA_VERSION}/numactl-${NUMA_VERSION}.tar.gz"
OUTPUT_DIR="${1:-$(dirname "$0")/../files/lib64}"

mkdir -p "$OUTPUT_DIR"
WORK_DIR=$(mktemp -d)
trap "rm -rf $WORK_DIR" EXIT

echo "=== Building libnuma ${NUMA_VERSION} ==="
echo "Output: $OUTPUT_DIR"
echo ""

echo "Downloading numactl-${NUMA_VERSION}..."
curl -sL "$NUMA_URL" | tar xz -C "$WORK_DIR"

cd "$WORK_DIR/numactl-${NUMA_VERSION}"

echo "Configuring..."
./configure --prefix="$WORK_DIR/install" --disable-static --quiet

echo "Building..."
make -j"$(nproc)" --quiet

echo "Installing to temp dir..."
make install --quiet

# Copy just libnuma.so.1
LIBNUMA=$(readlink -f "$WORK_DIR/install/lib/libnuma.so.1")
cp "$LIBNUMA" "$OUTPUT_DIR/libnuma.so.1"

echo ""
echo "=== Done ==="
echo "Installed: $OUTPUT_DIR/libnuma.so.1 ($(du -h "$OUTPUT_DIR/libnuma.so.1" | cut -f1))"
