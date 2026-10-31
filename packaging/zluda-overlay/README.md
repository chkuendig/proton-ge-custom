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
library dependency. Its direct `ntdll.so` dependency is a Wine provider: the
generated SDK exposes the already-built native object as `lib/ntdll.so`, requiring
ELF64 x86_64, SONAME `ntdll.so` and a defined `KeUserModeCallback` function.
SDK receipts hash that provider. Assembly requires the corresponding provider
under the tool's `files/lib/wine/x86_64-unix`, and the loader check rejects an
SDK/system copy or unresolved dependency. CPU runs add only that Wine directory
to the ordinary runtime paths; CUDA/ROCm directories stay excluded. Missing
callback/provider bindings must fail Wine's eager load. No extra SDK build is
needed. Ordinary PE imports must name allowed Windows system
runtimes or API-set DLL basenames. Delay-import directories are rejected,
as the pinned link contract never generates them. Declared PE directory
extents must fit the optional header; unbundled MinGW DLL dependencies
(such as libgcc_s_seh-1.dll, libgcc_s_dw2-1.dll and libwinpthread-1.dll) fail
both draft inspection and full packaging. Artifact checksums and BUILD-CONTRACT.json must identify
the exact locked source and toolchain. `provenance/ZLUDA-WINE.json` retains the
contract, checksums, inspection results and pending qualification statement.

Assembly copies the exact two empty builtin stubs from the checksum-locked GE
archive into `files/lib/wine-cuda/stub/{x86_64-windows,i386-windows}/nvcuda.dll`.
Their independently pinned hashes must match. The off root contains only those
regular PEs, with no Unixlib/backend alias. The unmodified SveSop nvidia-libs tree remains intact. Canonical CUDA modules
are excluded from Wine's dll_dir: Wine searches it before WINEDLLPATH, and the
canonical GE stubs prevent an ON launch from reaching ZLUDA (hosted run
41114561168). The default_pfx CUDA stubs are regular files, so GE's unchanged
copy block has valid source bytes without canonical modules. These two required
packaging residuals are audited along with both immutable roots.

Proton has three integration calls: bootstrap selects the stub root; one guard
around GE's unchanged nvcuda block restores template placeholders for ZLUDA-on
under the prefix lock; final configuration selects the launch root and applies
hardware/NVAPI policy after protonfixes. Off mode executes GE's block unchanged.
The four title fixes opt in during early(), before prefix setup, and respect an
explicit `PROTON_ENABLE_ZLUDA=0` (including on NVIDIA). In that case they also
leave the WoW64 choice to GE/the user. No automatic GPU-vendor probe is added.
Inherited managed roots are removed and exactly one root is placed first on
WINEDLLPATH. Only active ZLUDA forces nvcuda=b and receives the backend path.
The published dc2db071/e4dcac96 NVAPI timing is retained:
ZLUDA-on sets PROTON_FORCE_NVAPI, DXVK_NVAPI_ALLOW_OTHER_DRIVERS and
DXVK_ENABLE_NVAPI only after prefix setup. It does not insert `forcenvapi`, spoof
AMD as an NVIDIA GPU, change the driver version, undo `hidenvgpu` or repeat
prefix setup. An explicit early PROTON_FORCE_NVAPI still invokes GE's force
policy, including its GPU spoof. Published `disablenvapi` without an early
explicit force skips NVAPI DLL copies/native overrides even though ZLUDA-on
sets DXVK_ENABLE_NVAPI later; this behavior is deliberately preserved. Off-mode
NVAPI policy also matches the published launcher, including late game-fix behavior.
Off-mode overrides, prefix copies and native-NVIDIA flags follow stock GE.
With ZLUDA on, the guard skips only GE's nvcuda copies/override; conflicts with
PROTON_NVIDIA_NVCUDA/LIBS/NVOPTIX log one warning to stderr and the Proton log.
NVML/NVENC/NVOptiX retain GE policy, but CUDA-dependent combinations with ZLUDA
remain unqualified. Classic 32-bit ZLUDA launches warn that WoW64 is required.
A missing bundled backend falls back to GE. Corrupt stubs fail closed.


GE's original default-prefix creation and symlink handling are restored. There
is no custom Wineboot update or compatdata migration marker: GE's existing copy
block repairs every off launch, and the guarded on path restores placeholders
on each enabled launch. A real launcher transition checks both directions.
The remaining off-mode difference is the additional immutable stub root on
WINEDLLPATH, required to keep bootstrap and game namespace selection explicit;
it contains the same two byte-exact GE PEs and adds no override or Unix module.


`sources/zluda-wine.tar.gz` contains the full tracked ZLUDA superproject at its
pin, including cuda_macros' `ext/*-sys/src/lib.rs` inputs and backend attestation
sources. Compiler dep-info and build-script rerun-if records add only observed
submodule files, never whole LLVM or other submodule trees. Both CI and make use
`source_archive.py`: extract, run locked/offline Cargo metadata, resolve local
literal includes, then run an offline native library Cargo check to execute the
actual build-script/proc-macro regeneration from extracted sources alone. The
receipt records the source tree, archive hash, observed files and resolved includes.
The zluda_wine production crate at the clean-room pin is MIT OR Apache-2.0.
The SDK producer separately audits exact prepared-Wine callback, layout,
process/thread shutdown, TLS and loader-lock bodies and COM slots, plus the
strong local ntdll provider. Reviewer-only source contracts and failure
artifacts remain outside production. Shipping and draft builds consume only
the SHA256-pinned ABI-2 receipt and exact SDK binary/tool inputs, with explicit
source identity and SOURCE_DATE_EPOCH. Production does not read Wine source or
headers.

The source archive includes a portable prepared SDK/provider and ABI receipt,
which extracted builds remap and validate. Its production-closure include scan
uses compiler-observed sources and keeps test fixtures outside the production
input closure. The hosted offline triad rebuild
mounts neither the original checkout nor Wine source/object/SDK directories.
The full tracked clean ZLUDA source is archived with its complete production
provenance bundle. Prepared-Wine reviewer contracts retain Wine terms separately.
The runtime tool notice and SBOM identify production MIT OR Apache-2.0, keep
Wine/Proton under their own terms, and retain all provenance/ABI/receipt documents,
root MIT/Apache texts and Cargo notices.
Full hosted candidate gates include ABI, lifecycle, launcher and durable A1-layout
transitions. A separate W1 fixture job uses source at the same clean ZLUDA pin and the
matching fixture artifact from clean CI run 41216249728,
with correct PE32 widths chosen before execution, requiring 24/24 in both backend
modes and rejecting the old-width oracle. GPU, game and Deck W1/W2 remain pending. Full-source `make redist` builds the bridge after the prepared
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

The dxvk-nvapi and protonfixes gitlinks use GE's upstream repositories and pins.
GE preparation applies `patches/dxvk-nvapi/*.patch` and
`patches/protonfixes/*.patch` in filename order. The overlay builder applies the
same checksum-locked series in private source copies, leaving checkout HEADs
at their admitted upstream pins. Patch failures and differing resulting trees
are fatal. Offline fixtures prove that our two NVAPI and five game-fix patches
reproduce the original fork trees exactly, and that GE's two OptiScaler patches
commute with the game fixes. Prepared source archives include expanded recursive
submodules, the applied patch files, and upstream/prepared tree provenance.
NVAPI's generated VCS description now identifies the upstream pin with a dirty
suffix; its recorded prepared tree identifies the exact patched source.

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
hashes and all other GE i386 Wine files/links; the canonical PE32 CUDA stub is
excluded and its verified bytes live in the separate stub root and regular template. `provenance/WINE-SCOPE.json` records that inventory. Any different
prepared-source transformation retains the original two-architecture graph.
The PE32 CUDA, DXVK and NVAPI builds are independent and remain enabled.

After each Wine configure/tools stage, `wine_targets.py` reads the generated
top-level Makefile and validates every requested goal. Header/tool/module
aggregates are used only when declared; static and import SDK libraries use
their declared PE and native Unix archive rules. Every x86_64 profile requests
the native `dlls/ntdll/ntdll.so` SDK provider, alongside the separate import
libraries. The native-only profile does not rebuild PE ntdll modules for the overlay.
`log/targets-<arch>.json` records the Makefile hash and chosen goals; a compressed
copy of the actual Makefile is saved beside it even if goal validation fails. Target-name
fixtures are generated without configure from the pinned Wine generator and
Makefile.in inputs, including the absence of `winecrt0/all`.

`wine_sdk.py --generated` validates matching prepared source/generated headers
and executable Winebuild plus the native callback provider, then exports
`include`, `include-generated`, `bin/winebuild` and `lib/ntdll.so`.
It needs neither the retired Winelib cross-file nor winegcc
or relay import libraries. The original Winelib SDK mode and header probes
remain covered by historical baseline fixtures. SDK file hashes are recorded
in `log/wine-sdk.json`.

`bridge_tools.py --generated` exercises native C, Rust/Cargo, both MinGW
compiler/binutils sets, DXVK/NVAPI cross-file tools and the prepared Winebuild
before bridge configuration. `install-rust.sh` installs checksum-verified Rust
1.99 and both GNU Windows standard libraries into a private build prefix.
The upstream bridge entry fetches its independent locked graph and compiles
offline with at most three jobs. Both component jobs cache Cargo crate archives
and indexes, with keys covering the complete input lock, recipes, workflow and
Cargo locks; extracted registry sources and Git checkouts are never restored.

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
Partial cache saves remain enabled. LLVM ccache restore/save prefixes cover all
source pins and SDK/Rust versions through the complete input lock, all packaging
Python/shell recipes, LLVM's build script, Cargo lock and workflow. A unique run
suffix allows partial caches to grow. Cargo restores only crate archives/indexes;
fresh extraction verifies Cargo.lock checksums. The bounded 4 GiB compiler cache
retains no LLVM build tree. Corruption or changes to compiler inputs cannot be
made safe merely by a cache key; clean rebuild comparison remains a separate
reproducibility check. Package notices are collected per extracted
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

The bridges job compiles the pinned PE64 and PE32 CPU probes, lifecycle and
transition probes, and native ELF64 fake SysV backends with its prepared Wine
SDK. Assembly admits their checksummed archive, exact Proton/ZLUDA pins, SDK
receipt and source hashes before running the tool's own `files/bin/wine`. The profiles run sequentially with separate fresh
prefixes: `WINEARCH=win64` and `WINEARCH=wow64`, matching the launcher's WoW64
opt-in. Each profile runs six off/on/off and missing/relative backend modes.
The PE64 probe checks proc-address versions, the five-argument dark slot and
native-pthread callbacks invoking Windows TLS/heap APIs. The PE32 probe checks
its own 690-export surface, 32-bit handles/device offsets and bounded memory
roundtrip through the native WoW64 table. Both pass explicit fake backends
and exclude the tool's CUDA/ROCm library paths. The
container has no network or GPU devices, read-only source/tool mounts and a
private PID namespace and `/tmp`. Console/kernel APIs need no display; any
Wineboot/display diagnostics remain in the logs for the first hosted run.

`wine_cpu.py` admits the exact source and assembled provenance, checks each
probe's PE machine/header/imports and each backend's ELF64 x86_64 dependencies,
and requires both architecture receipts and all twelve cases, their logs and
successful cleanup. Its outer timeout/error guard kills and waits for each prefix-local
wineserver, accepts an already-exited server, then verifies absence with a
signal-zero probe and a direct nonblocking POSIX byte-range lock check. Any
surviving server, diagnostic on an absence result or lock-check error fails.
Failures block final tool artifact upload;
`wine-cpu-evidence` retains fixture source/binary hashes, toolchain versions,
probe binaries and separate win64/wow64 bootstrap/case/cleanup logs and JSON
results on either outcome. Each runner is bounded to 15 minutes; the combined
step is capped at 40 minutes for all runtime profiles and cleanup sequences.
Before upload, `stage_cpu_evidence.py` copies approved evidence into a fresh
directory using regular-file checks and directory-descriptor opens that never
follow symlinks. Prefix, drive mappings and disposable tool contents are excluded;
only the explicit migration receipt is copied from compatdata. `STAGING.json`
records hashes, missing/skipped paths and read errors even after a failed gate.
The upload action scans only this staged directory.
The PE64 gate and verified-absence cleanup passed hosted run 40994299565.
Both profiles passed hosted run 41003314309 on the prior 0f529f9d/6e3ff59a pair. A direct-Wine pass establishes
only the tested CPU behavior; real GPU W1/W2, all bounded opcodes under Wine,
complete graph/context ownership, interop and game adoption remain separate gates.

The same CPU step also requires a separate fresh-prefix PE64 lifecycle profile:
FreeLibrary and ExitProcess destructors under the loader lock, plus TLS thread
exit/self-removal. Its native event multiset and ordered PE callback trace must
match the pinned fixture contract, with independent prefix/server cleanup.
`lifecycle.exe`, its helper DLL, all three logs/traces and INTEGRATION.json are
retained. A separate no-op Unix-call timing probe records eleven pairs of raw
200,000-call QPC samples and a Windows indirect-call baseline; admission
recomputes the statistics but applies no performance threshold. This measures
only that pinned Wine transition, not CUDA or Deck performance. The ordinary
twelve cases stay unchanged.

The same 40-minute CPU step additionally invokes `launcher_cpu.py`, which runs
the actual assembled `proton` script off/on/off on one fresh compatdata per
architecture. Its disposable tool copy has no default_pfx initially and uses a
fixture in place of its bundled native backend; the release tool remains
unchanged. No WINEDLLPATH, CUDA override or prefix-stub repair is supplied by the
harness. It observes the final environment at Wine’s process boundary and checks GE
stub prefix/template bytes, native-NVIDIA copies/overrides, and on-mode NVIDIA
flag warnings across nine phases per architecture. The native-NVIDIA off phase
checks files/overrides only and never executes the relay without a GPU. PE32 uses the actual 35140 early/main protonfix via
`waitforexitandrun`; installed-verb log fixtures prevent installer/network use.
Both ordinary probes/events and every server cleanup remain mandatory. Hosted execution gates this launcher policy. `wine-cpu-evidence` always
retains its logs/results plus the exact three assembled production bridge
modules, BUILD-CONTRACT.json, SHA256SUMS and source-pin receipt, even on failure.
Evidence artifacts from failed runs are not release artifacts.

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

The native job also builds `compiler` offline after the backend/LLVM build and
retains `zoc-measurement-capsule`: exact zoc bytes, its observed ELF interpreter
and transitive SDK libraries, licenses, source/package receipts, and the public
test-only stack preload with source/toolchain hashes. CI uses no PTX input and
does not execute zoc. Later stack measurement needs a separately cleared local
command, an explicitly hashed local PTX module and `--arch gfx1030`. Game or
NVIDIA PTX must never enter GitHub artifacts or the source archive. The capsule
wrapper preserves preload support; the measurement script invokes its retained
loader explicitly. No Wine stack size override is applied.

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
Original-C dispatcher CPU comparisons and hosted ordinary PE32/PE64 Wine
execution are recorded in the evidence. Full GPU/private-table/callback parity
and target-game adoption remain pending. The package gate rejects missing or partial output without a C fallback;
passing compilation/assembly alone does not qualify adoption.

The workflow separately extracts the shipped `sources/zluda-wine.tar.gz` into a
fresh directory and runs locked, offline release Cargo builds for ELF64, PE64
staticlib and PE32 staticlib inside the pinned SDK with `--network=none`.
`REBUILD.json` binds the archive hash and all three output hashes; per-target
logs are retained in logs-bridges. Build scripts read the archive's Wine ABI
capsule and the prepared SDK, without a ZLUDA checkout mount.

Upgrade coverage also seeds both DLL locations in the A1 global-module layout,
with seed module hashes retained in each case receipt. Native DLL tripwires in
the game directory and prefix must lose to the selected ZLUDA builtin. The final
on-to-off launch requires Wine's existing update timestamp to be current and
unchanged; it must restore GE stub bytes without a prefix update. The optional
`historical_a1` workflow input downloads CI-built A1 from run 40906607864, creates
prefixes with that tool's own launcher, then upgrades them off/on. Receipts
identify all four original module hashes and the template links. Those are
CI-built A1 bytes, not the Deck-installed bytes. This optional input expires with
the historical artifact; the default gate retains its independent layout cases.

Manual harness iterations may set `components_run` to a previous work-branch run ID.
It defaults to empty (full rebuild) and has no release/default path. Admission rejects
changes to compiled sources, pins, locks or build recipes. The assembled tool records
the originating run, commit, artifact digests and downloaded archive hashes in
`provenance/COMPONENT-REUSE.json`; component provenance retains its original commit.
A frozen W1 candidate must use a full rebuild with reuse off, after the licence-lane
gitlink update. The pinned ABI/lifecycle fixtures run unchanged, including canonical namespace
admission: no CUDA module may appear in dll_dir. The durable upgrade cases are
labelled "A1-layout (stand-in modules)" and reject seeds already matching GE
stubs or the expected transition output.
