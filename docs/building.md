# Building proton-zluda

proton-zluda is a fork of GE-Proton (proton-ge-custom) with ZLUDA integration for CUDA-on-HIP translation. The full build produces a Proton compatibility tool that can be installed into Steam.

## Architecture Overview

The build has two parts:
1. **Standard Proton build** — Wine, DXVK, vkd3d-proton, etc. Built inside a Docker/Podman container using the Steam Runtime SDK.
2. **ZLUDA additions** — ZLUDA, ROCm runtime libs, wine_stack_fix.so, patched dxvk-nvapi. Built natively (outside the container).

The ZLUDA additions are placed into `files/lib64/` which gets bundled into the final Proton distribution tarball.

## Prerequisites

### For the full Proton build (container-based)
- **Docker or Podman** — the Proton build runs inside a Steam Runtime SDK container
- **Git + git-lfs**
- ~50GB disk space (Wine cross-compilation is large)

### For ZLUDA components (native)
- **CMake + Ninja** — for LLVM build
- **System GCC** — do NOT use ROCm clang (causes `logf128` type mismatch with LLVM 19)
- **Rust** — install via [rustup](https://rustup.rs/)
- **ROCm HIP runtime** — for linking (`libamdhip64.so`); either system install at `/opt/rocm` or fetched libs

## Quick Start: Full Build

### 1. Clone with submodules

```bash
git clone --recursive https://github.com/chkuendig/proton-ge-custom -b zluda-physx
cd proton-ge-custom
```

### 2. Pull LFS objects in ZLUDA submodule

```bash
cd zluda && git lfs pull && cd ..
```

Without this, the `.bc` bitcode files will be LFS pointers and the build will SIGSEGV parsing them.

### 3. Build ZLUDA additions

```bash
# Fetch ROCm runtime libraries (~190MB)
./rocm/fetch-rocm.sh

# Build libnuma (missing from Steam's pressure-vessel container)
./rocm/build-libnuma.sh

# Build ZLUDA + wine_stack_fix.so
./rocm/build-zluda.sh
```

**Note**: The first ZLUDA build compiles LLVM 19 from source (~4800 C++ objects). This takes 2-3 hours on Steam Deck (4 cores). Subsequent builds reuse the cached LLVM.

### 4. Build dxvk-nvapi (patched)

dxvk-nvapi requires a mingw-w64 cross-compiler:

```bash
cd dxvk-nvapi
meson setup --cross-file build-win64.txt build.win64
ninja -C build.win64
mkdir -p ../files/lib64/wine/nvapi/
cp build.win64/src/nvapi64.dll ../files/lib64/wine/nvapi/
cd ..
```

### 5. Build the full Proton distribution

This uses the standard proton-ge-custom build system (Docker/Podman container):

```bash
# Configure (downloads Steam Runtime SDK container image on first run)
./configure.sh --build-name="proton-zluda"

# Build everything and install to Steam
make install
```

Or to create a redistributable tarball:

```bash
make redist
# Output: build/proton-zluda.tar.gz
```

### 6. Install manually (if not using `make install`)

Extract the tarball to Steam's compatibility tools directory:

```bash
mkdir -p ~/.steam/root/compatibilitytools.d/
tar xzf build/proton-zluda.tar.gz -C ~/.steam/root/compatibilitytools.d/
```

Restart Steam, then select "proton-zluda" as the compatibility tool for your game in Steam's Properties > Compatibility.

## Quick Start: ZLUDA-Only (Overlay on Existing Proton)

If you already have GE-Proton installed and just want to add ZLUDA support without rebuilding all of Proton:

### 1. Build ZLUDA components as above (steps 1-4)

### 2. Copy ZLUDA files into an existing GE-Proton installation

```bash
PROTON_DIR=~/.steam/root/compatibilitytools.d/GE-Proton9-26

# ZLUDA (replaces/adds libcuda.so)
cp files/lib64/libcuda.so "$PROTON_DIR/files/lib64/"
cp files/lib64/libcuda.so.1 "$PROTON_DIR/files/lib64/"

# ROCm runtime
cp files/lib64/libamdhip64.so.* "$PROTON_DIR/files/lib64/"
cp files/lib64/libhsa-runtime64.so.1 "$PROTON_DIR/files/lib64/"
cp files/lib64/libamd_comgr.so.2 "$PROTON_DIR/files/lib64/"
cp files/lib64/libhsakmt.so.1 "$PROTON_DIR/files/lib64/"
cp files/lib64/libnuma.so.1 "$PROTON_DIR/files/lib64/"

# wine_stack_fix.so
cp files/lib64/wine_stack_fix.so "$PROTON_DIR/files/lib64/"

# Patched dxvk-nvapi
cp files/lib64/wine/nvapi/nvapi64.dll "$PROTON_DIR/files/lib64/wine/nvapi/"
```

### 3. Set launch options in Steam

For the game's Properties > Launch Options:
```
PROTON_ENABLE_ZLUDA=1 HSA_OVERRIDE_GFX_VERSION=10.3.0 %command%
```

### 4. Place dxvk.conf in the game directory

Copy `game-configs/dxvk.conf` to the game's executable directory (next to the .exe).

## Runtime Libraries

These are bundled in `files/lib64/` in the final distribution:

| Library | Size | Source | Purpose |
|---------|------|--------|---------|
| `libcuda.so`, `libcuda.so.1` | ~5M | ZLUDA build | CUDA→HIP translation |
| `libamdhip64.so.6` | 24M | ROCm HIP runtime | HIP runtime |
| `libamdhip64.so.5` | 24M | Copy of .so.6 | Version compat shim |
| `libhsa-runtime64.so.1` | 2.8M | ROCm HSA runtime | GPU kernel dispatch |
| `libamd_comgr.so.2` | 163M | ROCm code object manager | GPU code compilation |
| `libhsakmt.so.1` | 181K | ROCm kernel mode thunk | /dev/kfd interface |
| `libnuma.so.1` | ~55K | numactl (built from source) | NUMA topology (ROCm dep) |
| `wine_stack_fix.so` | ~8K | Built from `patches/rocm/` | ROCm/clr#61 workaround |
| `wine/nvapi/nvapi64.dll` | ~500K | dxvk-nvapi build | NVIDIA API spoofing |

### Why libnuma must be built from source

`libnuma.so.1` is a transitive dependency of ROCm's HSA runtime. It exists on the host SteamOS system but is **not available inside Steam's pressure-vessel container**. When ZLUDA tries to `dlopen("libcuda.so")` with `RTLD_NOW`, the missing libnuma causes a silent failure — no error message, just no CUDA. Diagnosed via `LD_DEBUG=files`.

Build it with:
```bash
./rocm/build-libnuma.sh
```

This downloads numactl from GitHub, builds `libnuma.so.1`, and places it in `files/lib64/`.

## Known Gotchas

1. **LLVM submodule version**: Must be vosen's LLVM 19 fork (commit `fd25b11e`). Wrong version causes SIGSEGV parsing `.bc` files.

2. **Git LFS**: If you see crashes in `Module::try_from_bitcode`, the `.bc` files are probably LFS pointers. Run `git lfs pull` in the `zluda/` submodule.

3. **Compiler**: System GCC works. ROCm clang 17 has a `logf128` type incompatibility with LLVM 19 headers.

4. **LIBRARY_PATH**: If the linker can't find `libamdhip64.so`, set:
   ```bash
   export LIBRARY_PATH=/opt/rocm/lib
   ```

5. **Symlinks don't work in pressure-vessel**: Steam's container runtime can't follow symlinks to host paths. Always use real file copies in `files/lib64/`, never symlinks.

6. **Docker on Steam Deck**: SteamOS doesn't ship Docker. Install Podman via `sudo pacman -S podman` (requires `steamos-readonly disable`), or use distrobox.
