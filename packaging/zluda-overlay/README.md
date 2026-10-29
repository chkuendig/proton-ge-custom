# Pinned GE11-7 ZLUDA overlay

This is the CI version of the public hub's `build-public-artifacts.sh` and
`install-zluda-tool.sh` recipe (hub revision `67e7788a`). It creates a compatibility
tool archive; it does not install into Steam or touch a prefix. The existing Deck
runbooks and installed tool are untouched. This is packaging step 1: `make redist` now
copies `proton_zluda.py`, but full ZLUDA component dependencies in `all-dist` remain
separate work.

The A2 bridge entry is `zluda/wine/zluda_wine` in the pinned ZLUDA checkout.
It builds the independent Rust lock for native x64, x64 GNU Windows and i686
GNU Windows. It never substitutes the Stage A C relay or packages an incomplete
pilot. The input lock records the crate tree and independent Cargo.lock hash.
The present foundation source remains under implementation: a successful file
inventory is not evidence of callback/dark-table/interop or PE32 ABI parity.
C/Rust equivalence and target-runtime adoption gates remain required.

Both overlay and full-source bridge targets install three unchanged modules:
`files/lib/wine-cuda/zluda/{x86_64-windows,i386-windows}/nvcuda.dll` and
`files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so`. Both PEs must carry Wine's
builtin marker, the matching machine and 788/690 named exports respectively;
the ELF must expose native and WoW64 Unix-call tables and have no direct GPU
library dependency. Artifact checksums and BUILD-CONTRACT.json must identify
the exact locked source and toolchain. `provenance/ZLUDA-WINE.json` retains the
contract, checksums, inspection results and pending qualification statement.

Assembly copies the exact two empty builtin stubs from the checksum-locked GE
archive into `files/lib/wine-cuda/stub/{x86_64-windows,i386-windows}/nvcuda.dll`.
Their independently pinned hashes must match. The off root contains only those
regular PEs, with no Unixlib/backend alias. All nvcuda forms are removed from
canonical `files/lib/wine`, including its optional `nvidia-libs/nvcuda` tree and
`lib64/wine` alias. Both default-prefix nvcuda placeholders become regular stub
bytes, avoiding GE's now-dangling relative symlinks. A namespace audit runs
before manifests/archive generation; any extra or missing module fails.

Proton bootstraps Wineboot/prefix setup using only the stub root. Immediately
before game launch, PROTON_ENABLE_ZLUDA=1 selects only the generated root;
otherwise the stub root remains selected. Inherited managed roots are removed,
one builtin CUDA override is merged, and only enabled processes receive the
bundled backend's absolute filename internally. That filename is implementation
state, not a user setting. Published one-switch NVAPI behavior is retained;
off-mode NVAPI policy matches GE, including late game-fix behavior. Known managed
prefix PE placeholders, ELF copies and tool symlinks migrate once under the
prefix lock; native unmarked user DLLs/unknown files are preserved and logged.

Sources include the complete pinned `wine`, `cuda_types` and `cuda_macros`
regeneration inputs in `sources/zluda-wine.tar.gz`. Root MIT/Apache, derived LGPL
and per-file notices, the independent Cargo lock and downloaded dependency
licenses are retained. Stage A comparison utilities remain available for source
oracle work; they are no longer invoked as a substitute for generated-bridge
qualification. Full-source `make redist` builds the bridge after the prepared
Wine SDK and installs after both Wine architectures; backend/ROCm provisioning
still belongs to the separate native component.

`inputs.lock.json` pins the GE11-7 x86_64 release, seven official AMD ROCm 7.2.4
noble packages, Rust 1.99.0, numactl 2.0.18 source, Steam Runtime SDK image digest,
component gitlinks, patched LLVM gitlink, Cargo.lock hash and all four ZLUDA LFS
objects. GE's archive hash comes from its GitHub release asset digest; AMD package
hashes come from the checksum-pinned `Packages.gz`. The LLVM configuration remains
the committed `llvm_zluda/build.rs` AMDGPU/LLD configuration, not the SDK's LLVM.
Cargo fetch/build use `--locked`, with an offline compilation after fetch.

The manual workflow builds the Linux ZLUDA/LLVM side and the Wine/bridge/PE side
in separate jobs. Each initializes only its required top-level components and
**every recursive submodule in those components**, fetches LFS, and requires each
HEAD to equal its parent gitlink. Unrelated full-Proton inputs are not checked out
or rebuilt. Assembly checks pinned protonfixes and both intermediate manifests and
requires both overlays to come from the same Proton commit and input lock.

## Local interface

Use a clean, committed recursive checkout and a suitable Linux x86_64 build SDK.
Python 3.12+ is required for archive extraction filters; the manifest and admission
helpers/tests also run on Python 3.11. All paths must be absolute. A build runs
serial stages with `JOBS` defaulting to 3 and rejecting values outside 1..3.

```bash
export SOURCE_ROOT=/src/proton-ge-custom
export BUILD_ROOT=/work/build DOWNLOAD_ROOT=/work/downloads
export SDK_ROOT=/work/sdk/opt/rocm-7.2.4
export WINE_SDK_ROOT=/work/wine-sdk DESTDIR=/work/overlay
export BUILD_PART=all JOBS=3 CARGO_HOME=/work/cargo CCACHE_DIR=/work/ccache
bash "$SOURCE_ROOT/packaging/zluda-overlay/build-components.sh"
```

`BUILD_PART=zluda` or `bridges` creates one half instead. `SDK_ROOT` must not
already exist: verified AMD packages are extracted without installation there.
`WINE_SDK_ROOT` receives wrappers pointing to the prepared Wine build's tools and
headers. The build SDK needs GCC/G++, both MinGW GCC toolchains, Wine's native and
32-bit build dependencies, Meson, Ninja, CMake, ccache, autotools, bison, flex,
Python and glslang. CI uses the exact GE Steam Runtime 4 SDK digest in the lock;
it does not install mutable distro packages. CI itself runs on an ephemeral
GitHub-hosted Ubuntu runner.

`build-wine.sh` retains the hub's two prepared-tree comparison, changed-module
selection, x86_64/i386 builds, and GE ELF NEEDED/version checks. Its GE baseline
and archive name come directly from the input lock; release tags are optional,
but the locked baseline commit must be present and an ancestor of the checkout.
This source pin
changes `ntdll`, so silently retaining GE's Wine would lose a shipped fix. The
same prepared Wine source supplies the generated bridge's source/generated
headers and `winebuild`. WoW64 is an i686 PE plus native x86_64
unixlib; no i386 HIP/ROCm library is built or shipped. Any missing component or
required runtime license collection fails the job. No failed relay is treated as success.

`wine_deps.py` generates a focused Makefile from this checkout's `Makefile.in`
and reuses its dependency recipes, per-architecture Wine environment and configure
flags, including required Wayland, FFmpeg and warnings-as-errors. It builds GE's
complete x86_64/i386 Wine prerequisite closure (XML/XKB/pcap, media, speech,
VKD3D and OpenXR), plus the architecture-independent wayland-protocols data.
`wine-deps.json` records the direct/transitive dependency contract, including
empty and per-architecture edge sets. Tests compare the focused GNU Make graph
with the expanded GE graph, including configure/build/post-build edges and
Kaldi's cross-architecture ordering. The workflow checks out and validates all those source pins
from the input lock. Dependencies run one component at a time with at most three
jobs inside the component. The prepared Wine source is preserved rather than
being overwritten by the source-copy rule.

For the exact prepared-source delta adding `leave_handler(ucontext)` to the
x86_64 Unix steamclient fault redirect, `wine_scope.py` selects a native-only
profile. It skips i386 Wine/prerequisites, removes Kaldi's now-unneeded cross-arch
serialization, and overlays only `x86_64-unix/ntdll.so`. Both PE ntdlls and the
classic i386 Unix ntdll come from the locked GE archive. Assembly verifies their
hashes and all other GE i386 Wine files/links; the canonical PE32 CUDA stub is intentionally removed after its bytes are
verified and retained in the separate stub root. `provenance/WINE-SCOPE.json` records that inventory. Any different
prepared-source transformation retains the original two-architecture graph.
The PE32 CUDA, DXVK and NVAPI builds are independent and remain enabled.

After each Wine configure/tools stage, `wine_targets.py` reads the generated
top-level Makefile and validates every requested goal. Header/tool/module
aggregates are used only when declared; static and import SDK libraries use
their declared PE and native Unix archive rules. The native-only profile requests
the native `dlls/ntdll/ntdll.so` producer, alongside the separate SDK import
libraries. It does not request PE ntdll module rebuilds for the overlay.
`log/targets-<arch>.json` records the Makefile hash and chosen goals; a compressed
copy of the actual Makefile is saved beside it even if goal validation fails. Target-name
fixtures are generated without configure from the pinned Wine generator and
Makefile.in inputs, including the absence of `winecrt0/all`.

`wine_sdk.py --generated` validates matching prepared source/generated headers
and executable Winebuild, then exports `include`, `include-generated` and
`bin/winebuild`. It needs neither the retired Winelib cross-file nor winegcc
or relay import libraries. The original Winelib SDK mode and header probes
remain covered by historical baseline fixtures. SDK file hashes are recorded
in `log/wine-sdk.json`.

`bridge_tools.py --generated` exercises native C, Rust/Cargo, both MinGW
compiler/binutils sets, DXVK/NVAPI cross-file tools and the prepared Winebuild
before bridge configuration. `install-rust.sh` installs checksum-verified Rust
1.99 and both GNU Windows standard libraries into a private build prefix.
The upstream bridge entry fetches its independent locked graph and compiles
offline with at most three jobs. Both component jobs cache downloaded Cargo
sources even after failures, separated by component and lock hashes.

The final overlay's `DESTDIR` is private to the assembly helpers. Upstream builds
must not inherit it: GE dependency recipes already install to absolute private
prefixes, and inherited staging would prepend the overlay path a second time.
The generated Makefile also clears it for standalone use. A tiny install fixture
exercises GE's OpenFST recipes with an exported overlay destination.

Speech dependencies remain in the graph. Wine's `configure.ac` detects Vosk for
`windows.media.speech` and Piper for `protontts`; Kaldi and OpenFST support Vosk.
Those modules are outside the current `ntdll` delta, but retaining GE's complete
prerequisites preserves configure results and supports future module selection.

The native stage explicitly compiles `steamos-math-compat.c`, builds libnuma from
pinned source, and installs only the seven versioned ROCm libraries listed in the
lock plus their declared SONAME/development aliases. Link-only math stubs are
build inputs and are never installed. `build-native.sh` resolves the final Linux
`-p zluda` graph using locked Cargo metadata and its normal/build tree. `native_plan.py` intersects that graph with
safe early workspace roots and uses their exact package IDs and resolved features.
Missing required roots fail before compilation; optional registry dependencies
are reported as absent, covered transitively or deferred to the final build.
They are never guessed as independent `-p` arguments. Roots that reach LLVM are
excluded from the early stage. The stage verifies HiGHS's archive/search paths
and the emitted ROCm links, then builds LLVM alone before the final ZLUDA package.
It retains `cuda_types`' feature selection, including hipfft's disabled link feature.
`native/metadata.json`, `zluda-tree.txt` and `plan.json` retain the actual resolver
inputs and decisions in the logs artifact. The fixture projects package identities
and dependency edges from the pinned Cargo.lock; it does not claim to be a captured
Cargo invocation.
`native-cmake.py` passes `CMAKE_INSTALL_LIBDIR=lib` only when configuring HiGHS;
it also removes staging DESTDIR. This leaves the ZLUDA pin and LLVM/Wine flags
unchanged. Native logs and `NATIVE-LINK-LAYOUT.json` record the actual layout.
The workflow restores compiler/Cargo source caches by compatible prefixes and
explicitly saves them even after failure, with unique immutable keys per run.
Only the bounded 4 GiB compiler cache is retained for LLVM, not its large build
tree. GitHub's cache eviction controls accumulated snapshots; cache upload and
reuse still need real CI validation. Package notices are collected per extracted
AMD package; component and Cargo dependency licenses are retained. Corresponding
libnuma, generated bridge and prepared Wine source and this build recipe are included under
`sources/`. GE's existing license payload remains in the archive. Inspect the
actual first CI inventory and any component-specific source/NOTICE obligations
before publishing binaries; this recipe has not certified an unseen SDK payload.
`hip-dev` and `hsa-rocr-dev` supply build headers only: missing notices in those
two packages are allowed and recorded with their package identities in
`provenance/ROCM-PACKAGES.json`. Runtime packages must own their declared shipped
libraries and provide notices. AMD's notices under `opt/rocm-7.2.4/share/doc/` and
Debian notices under `usr/share/doc/` are both collected recursively.

## Layout and verification

GE's canonical Wine modules stay under `files/lib/wine`. `files/lib64/wine` is a
relative alias to that tree. CUDA and ROCm stay in `files/lib64`, with CUDA aliases
in `files/lib/wine/x86_64-unix`. The launcher's existing enabled prepend operations
and their order are preserved, including their precedence over
`user_settings` loader paths.

`MANIFEST` contains SHA-256 for every regular file except itself. The hashed
`SYMLINKS.json` records every symlink, including directory aliases. Newly built
links must stay within the tool and resolve. Any unchanged external or dangling
links in GE's trusted prefix template are explicitly recorded in
`provenance/GE-TEMPLATE-LINKS.json`; neither hashing nor size accounting follows
those links. Extraction rejects paths that would write through an external link.

Both intermediate overlays are tarred before artifact upload to preserve links.
Final output is one `GE-Proton11-7-zluda.tar.gz`, standalone `MANIFEST`,
`SHA256SUMS`, size report and separate build/assembly/loader logs. The archive
contains `licenses/`, `sources/`, and minimal JSON SBOM/provenance files under
`provenance/` (these do not claim SPDX certification). File modes and links are
retained, tar ordering/ownership/timestamps and gzip timestamps are normalized.
This is a pinned recipe; bitwise reproducibility requires two clean builds.

The final CI job checks transitive ELF dependencies inside the pinned SDK and
records its output. The SDK has more libraries than a game runtime: this is not
pressure-vessel, enabled/disabled launcher, or GPU qualification.

```bash
# Small metadata + HEAD requests only; no SDK layers or GE/Rust/ROCm payloads.
python3 packaging/zluda-overlay/inputs.py resolve /tmp/overlay-pin-check
python3 -m unittest discover -s tests -v
for script in packaging/zluda-overlay/*.sh; do bash -n "$script"; done
shellcheck packaging/zluda-overlay/*.sh
actionlint .github/workflows/zluda-overlay.yml
# After extracting a produced tool:
python3 packaging/zluda-overlay/inputs.py verify /path/to/GE-Proton11-7-zluda
```

## CI resources and first run

Only `workflow_dispatch` triggers `.github/workflows/zluda-overlay.yml`. It never
pushes, publishes a release, or runs on a push/PR. Component jobs cap at 350 minutes;
assembly caps at 90 minutes. Build invocations use at most three compiler jobs.
The SDK build requires 30 GB free after image pull and source checkout. Runner
cleanup removes only preinstalled tools on that ephemeral runner. LLVM ccache is
bounded at 4 GiB and keyed by patched LLVM pin, SDK digest, Rust version and build
configuration. Cargo download caches are separate; compiled Cargo target/LLVM
trees and extracted SDK/GE inputs are not persisted in the cache. Intermediate
artifacts expire after seven days; final output/logs after fourteen days.

Allow approximately 3–6 hours elapsed for the first cold run (jobs overlap), and
roughly 1–3 hours with a useful LLVM cache. These are planning estimates, not
measurements of these pins. The complete GE Wine prerequisite graph now needs
fresh timing; the earlier estimate did not measure those dependencies. A cold job hitting its cap requires a CI follow-up;
it is not permission to increase local build concurrency. First CI must establish
SDK package layouts/notices, Wine/MinGW compatibility, duration, disk/RAM use,
loader output, and archive sizes. Target-runtime and game qualification follow
separately when the device is available.

## Focused x64 compile draft

Manual dispatch `profile=x64-draft` runs only the bridges job. It prepares the
same pinned Wine SDK and Rust toolchains, then invokes ZLUDA's `build-pilot.sh`
with a fresh `PILOT_BUILD_ROOT`. It uploads `zluda-wine-x64-draft` containing
only builtin PE64, native Unixlib, hashes, the explicit linker dependency log
and `X64-COMPILE-DRAFT.json`. Normal build and Wine logs remain separate.
A successful draft records compilation and basic file checks, not packaging
or ABI/runtime parity. Export-count differences are recorded for review;
PE32 and the WoW64 table are not required for this draft. The complete package
validator still requires all three modules, 788/690 exports and both tables.

Draft mode does not build the native CUDA/LLVM backend, generate an overlay
tarball, install modules into a tool or run the assembly job. Default dispatch
`profile=overlay` retains the full three-module path. The pinned WoW64 draft
now supplies both PE frontends, all 59 bounded operations and both ELF tables.
Its 13 native CPU tests include original-C dispatcher comparisons; actual
PE32/PE64 compilation/loading and Wine/GPU/private-table/callback parity remain
pending. The package gate rejects missing or partial output without a C fallback;
passing compilation/assembly alone does not qualify adoption.
