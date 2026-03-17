# ROCm Stub Libraries for ZLUDA Build

## Why stubs are needed

ZLUDA's `cargo xtask` builds all packages including optional wrappers for cuBLAS, cuDNN, cuSPARSE, and cuFFT. These wrappers link against ROCm math libraries (rocBLAS, MIOpen, hipBLASLt, rocSPARSE) that are **not needed for PhysX** but are required to satisfy the linker.

On Steam Deck (SteamOS), only the base ROCm HIP runtime is available at `/opt/rocm/lib/`. The full ROCm math stack would require ~2GB of additional packages and `steamos-readonly disable`.

## Solution: Empty stub libraries

Create empty shared libraries that satisfy the linker at build time:

```bash
mkdir -p /tmp/rocm-stubs
for lib in rocsparse MIOpen hipblaslt rocblas; do
    echo "" | cc -shared -x c - -o /tmp/rocm-stubs/lib${lib}.so
done
```

Then build with both library paths:

```bash
LIBRARY_PATH=/opt/rocm/lib:/tmp/rocm-stubs cargo build --release -p zluda -p llvm_zluda -p ptx
```

## What's safe about this

The stub libraries are **only used at link time**. At runtime:

- `libnvcuda.so` (the ZLUDA CUDA driver) only calls into `libamdhip64.so` and `librocm_smi64.so`
- The cuBLAS/cuDNN/cuSPARSE wrappers (`libcublas.so`, `libcudnn*.so`, `libcusparse.so`) are never loaded by PhysX
- PhysX only uses core CUDA driver APIs: `cuInit`, `cuModuleLoadData`, `cuModuleGetFunction`, `cuLaunchKernel`, `cuMemAlloc`, etc.

## When you DON'T need stubs

If you have a full ROCm install (e.g., on a desktop Linux system):

```bash
# Install ROCm math libs (Arch/Manjaro)
sudo pacman -S rocblas miopen-hip hipblaslt rocsparse

# Or on Ubuntu
sudo apt install rocblas-dev miopen-hip-dev hipblaslt-dev rocsparse-dev
```

With those installed, `LIBRARY_PATH=/opt/rocm/lib cargo xtask --release` will build everything including the math library wrappers.

## Alternative: Build only needed packages

Instead of stubs, you can build just the packages PhysX needs:

```bash
LIBRARY_PATH=/opt/rocm/lib cargo build --release -p zluda -p llvm_zluda -p ptx
```

This skips the math wrappers entirely. The only output needed for PhysX is `target/release/libnvcuda.so`.

Note: `cargo xtask` always builds all packages and will fail without the math libs. Use `cargo build -p` to build selectively.
