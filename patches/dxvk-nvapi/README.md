# Provider-agnostic CUDA topology series for dxvk-nvapi

Base: upstream jp7677/dxvk-nvapi `fd105c0d07f4531169eba4d1d1eaa25f78a416a2`.
Apply the seven patches in order with `git am`. They replace the whole
2026-10-30 series (old 0001–0004); do not combine the two. Patch 0001 is the
old 0001 (same commit `d42d966`). Patch 0005 is a test-only fix: the new tests
in 0002–0004 fail without it. It was added on top because the series was
already on the review branch `review/cuda-topology-provider-20261031`. Squash 0005, 0006 and 0007
into 0002–0004 before any upstream submission. Upstream submodule pins are unchanged.

1. `0001` – V1 fixed-array bounds protection (unchanged).
2. `0002` – Lazy CUDA discovery. On the first topology query only, load
   `nvcuda.dll`, call `cuInit(0)`, `cuDeviceGetCount` and `cuDeviceGet`, then
   match every CUDA device against the DXVK adapters (by LUID in 0002, by PCI
   location from 0006). Matches come first, in CUDA ordinal order, followed by
   the adapters upstream's heuristic already reports (proprietary NVIDIA,
   Maxwell or newer). If `nvcuda.dll` is missing, fails, or matches nothing,
   the result is exactly upstream's. The result is cached per registry until
   the final `NvAPI_Unload`. It is never built in `NvAPI_Initialize`/DllMain or
   under the initialization lock. A provider that calls back into NVAPI on the
   discovering thread gets the uncached heuristic answer.
3. `0003` – Public topology V2, output-only. It is accepted only when CUDA
   confirmed at least one GPU; otherwise it is rejected as upstream does. It
   returns the cached registry-owned array. CUDA-confirmed entries carry
   `0x21001b` in V2, and V1 stays `0x0b`. These are the flags of the published
   tool.
4. `0004` – The undocumented `0x36e39e6b`, named
   `NvAPI_CUDA_EnumComputeCapableByTopology`. It is a variadic cdecl that reads
   only the first pointer and leaves the version untouched. It returns the same
   array as V2, or `NVAPI_NO_IMPLEMENTATION` without CUDA-confirmed GPUs. It
   also gets its own disable name and validator coverage, and the real
   dispatcher is linked into the unit tests (fresh-process disable tests via
   `meson test`).
5. `0005` – tests: set `DXVK_NVAPI_ALLOW_OTHER_DRIVERS` in every section,
   because the Catch section listener clears it. Also clang-format fixes.
   Production code is unchanged.
6. `0006` – match CUDA devices by PCI domain/bus/device (`cuDeviceGetAttribute`,
   CUDA 4.0) instead of `cuDeviceGetLuid` (CUDA 10). This is the same identity
   used for NVML. `cuDeviceGetLuid` is dropped from the loader. Squash it into
   0002 (and its test edits into 0002–0004) before upstream.

7. `0007` – test-only: give the mock adapters valid LUIDs in the section
   checking that unrelated NVAPI methods do not load CUDA. Production code is
   unchanged (`a83d885`).

Packaging: nothing besides upstream's `DXVK_NVAPI_ALLOW_OTHER_DRIVERS=1`.
Non-NVIDIA adapters need it to be admitted at all, so they can be matched.
The CUDA provider is detected at runtime; no provider-specific switch is required.
There is no ZLUDA- or vendor-specific code in the series.

Dependency: an adapter is reported only if the PCI domain/bus/device that
CUDA reports (`cuDeviceGetAttribute` 50/33/34) equals its `VK_EXT_pci_bus_info`
location. Both bridges already forward these attributes to HIP, and no CUDA 10
API is needed. HIP-versus-RADV equality on the Deck is still unverified.

Proton test harness: the PE32 test adapter (`nvapi_package.py adapt-pe32`) must
add `nvapi_sysinfo_cuda.cpp` to its kept test sources. The disable checks keep
the test name `PhysX private interface lookup`.

Historical validation before PCI patch 0006 (tip `8f3def5`, Wine 9.0, MinGW GCC 13-posix): x64 unit tests pass
(33,740 assertions in 28 cases) plus 3 fresh-process disable checks.
- PE32: the DLL builds, the adapted test build passes (7,684 assertions in 13
  cases, all 6 CUDA cases included), and the 3 disable checks pass.
- `validate-methods.py` passes and clang-format reports no changes.

The seven-patch integration requires fresh hosted x64/PE32 unit tests.

See workspace `evidence/dxvk-nvapi-provider-20261031/REPORT.md` for design, flag answer, test matrix and hashes.
