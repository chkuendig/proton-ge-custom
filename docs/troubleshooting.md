# Troubleshooting

## PhysX GPU options are greyed out

**Cause**: PhysX can't detect a CUDA-capable GPU.

**Checks**:
1. Verify `DXVK_NVAPI_ALLOW_OTHER_DRIVERS=1` is set
2. Verify dxvk-nvapi is the patched version (removes `HasNvProprietaryDriver()` gate)
3. Verify `dxvk.conf` is in the game's executable directory with vendor/device ID spoofing
4. Check `PROTON_FORCE_NVAPI=1` is set
5. Check ZLUDA debug log: `cat /tmp/zluda_debug.log`

## dlopen("libcuda.so") fails silently

**Cause**: Missing dependency — usually `libnuma.so.1`.

**Debug**: Run with `LD_DEBUG=files` to see which dlopen fails:
```bash
LD_DEBUG=files PROTON_LOG=1 %command%
```

**Fix**: Bundle `libnuma.so.1` in `files/lib64/`.

## ROCm assertion failure / stack corruption

**Cause**: ROCm's HSA runtime gets confused by Wine's thread stack layout (ROCm/clr#61).

**Fix**: Ensure `wine_stack_fix.so` is in `LD_PRELOAD`. Check the proton log for "ZLUDA enabled" message.

## PTX compilation hangs or crashes

**Cause**: LLVM version mismatch or missing `.bc` files.

**Checks**:
1. Verify LLVM submodule is at the correct commit: `cd zluda/ext/llvm-project && git log --oneline -1`
2. Verify `.bc` files are real (not LFS pointers): `file zluda/target/release/build/*/out/*.bc`
3. Check compile log: `cat /tmp/zluda_compile.log`

## cuDeviceGetAttribute returns INVALID_VALUE for unknown attribute

**Cause**: PhysX 3.x queries non-standard attribute `0x20080402`. This is a closed-source PhysX extension.

**Status**: ZLUDA correctly returns `CUDA_ERROR_INVALID_VALUE`. PhysX continues past it — this is expected behavior.

## Game crashes in APEX_TurbulenceFS_x64.dll

**Status**: Known issue. APEX turbulence uses `cuGraphicsMapResources` / `cuGraphicsSubResourceGetMappedArray` for D3D11-CUDA interop, which is not yet implemented in ZLUDA.
