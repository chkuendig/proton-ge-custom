# Pinned GE11-7 ZLUDA overlay

This is the CI version of the public hub's `build-public-artifacts.sh` and
`install-zluda-tool.sh` recipe (hub revision `67e7788a`). It creates a compatibility
tool archive; it does not install into Steam or touch a prefix. The existing Deck
runbooks and launcher are unchanged. This is packaging step 1: `make redist` now
copies `proton_zluda.py`, but full ZLUDA component dependencies in `all-dist` remain
separate work.

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
same prepared Wine source supplies the relay's headers, `winegcc`, `winebuild`,
import libraries and resource compiler. WoW64 is an i686 PE plus native x86_64
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
hashes and all other GE i386 Wine files/links; the PE32 CUDA relay is an explicit
replacement. `provenance/WINE-SCOPE.json` records that inventory. Any different
prepared-source transformation retains the original two-architecture graph.
The PE32 CUDA, DXVK and NVAPI builds are independent and remain enabled.

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
build inputs and are never installed. Package notices are collected per extracted
AMD package; component and Cargo dependency licenses are retained. Corresponding
libnuma, relay and prepared Wine source and this build recipe are included under
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
in `files/lib/wine/x86_64-unix`. Thus the launcher's existing prepend operations
and their order remain byte-for-byte unchanged, including their precedence over
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
