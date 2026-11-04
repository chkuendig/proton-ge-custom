# ZLUDA Proton packaging

This repository builds a compatibility tool from a pinned Proton source tree,
ZLUDA backend, permissively licensed Wine CUDA bridge and component patch series.
It produces archives and receipts. Installation and game qualification are
separate operations.

## Inputs

Select a base explicitly with `ZLUDA_PACKAGING_PROFILE=ge-11-7`.
`profiles/<profile>.json` selects the Proton repository and commit, immutable
input lock, source epoch and admitted base archive/stub hashes. Shared runners
require that profile; unknown base identities fail admission.

The input lock fixes the SDK image digest, recursive component commits, backend
LLVM commit, independent bridge Cargo lock and source tree, downloads, patches
and license inventory. The `proton-source` gitlink pins the launcher tree.
`PROTON_SOURCE_ROOT` can select a checkout of that same committed source.

The bridge consumes six digest-checked declaration/admission files from the
separate backend through `ZLUDA_WINE_BACKEND_SOURCE`. Its generated schema and
three modules build together. `ZLUDA_WINE_CALL_LOG` provides optional PE32 tracing.

## Hosted build

Dispatch `.github/workflows/zluda-overlay.yml` with the selected base profile.
Its static gate precedes component builds. Native scope and PE32 LUID fixtures
run independently. The locked Steam Runtime SDK builds the native backend and
bridge/component overlays with at most three build jobs per job.

The bridge job prepares Wine from the selected Proton patch series. An exact
native ntdll source transformation permits an x86_64 Unix ntdll rebuild while
preserving both PE ntdlls and classic i386 Wine files. Other source changes retain
the complete architecture graph. ELF dependency and symbol-version gates compare
against the admitted base archive.

CPU ABI and W1 identity/width fixtures build from the same bridge checkout and
Wine SDK as the shipped modules. Their receipts bind source pin, SDK image,
input lock, base profile, fixture sources and executable hashes. No external
fixture artifact supplies the W1 gate.

Assembly verifies each component MANIFEST, base download digest, patch series,
required modules, exported entry points, builtin markers and loader dependencies.
The CUDA layout is:

```
files/lib64/libcuda.so.1
files/lib/wine-cuda/zluda/x86_64-unix/nvcuda.so
files/lib/wine-cuda/zluda/x86_64-windows/nvcuda.dll
files/lib/wine-cuda/zluda/i386-windows/nvcuda.dll
files/lib/wine-cuda/stub/{x86_64-windows,i386-windows}/nvcuda.dll
files/lib/wine-cuda/stubs.json
```

The base profile admits both off-mode placeholders. Enabled prefixes receive
regular bridge files; disabled prefixes receive the exact base placeholders.
Canonical Wine module paths remain absent from the overlay. The launcher owns
prefix selection and transition policy in the pinned Proton tree.

## Source and notices

Every regular shipped member is listed in MANIFEST. SYMLINKS.json records link
targets. The archive, external MANIFEST and checksum files accompany the tool.
Component receipts record pins, input hashes, compiler versions, build packages,
patches, source archives and license paths.

The bridge capsule contains its committed source and admitted backend closure,
the exact Wine SDK/provider, ABI receipt and build recipes. Compiler dependency
observations bind the compiled source closure. The SDK job rebuilds the capsule
offline and verifies required exports. The capsule is a rebuild input, not a
substitute for runtime qualification.

`ZLUDA-WINE-NOTICE.md` and `licenses/zluda-wine/` include the MIT/Apache notices,
production-input inventory and original provenance. License collection retains
component and Cargo notices. Public sources and fixtures contain no proprietary
driver or game bytes.

`SOURCE-ORIGIN.json` and `BRIDGE-ORIGIN.json` identify reviewed tooling and fixture
dispositions by original Git blob. Unlisted bridge files have no shrink/removal
permission. Private fixture archives are outside this repository.

## Release gates

`product_identity.py` compares the candidate tar and MANIFEST against the
immutable published d32c4524 product. Every runtime member must preserve content,
type, mode, owner, timestamp and link identity. It enumerates source/support
changes individually. Unknown differences fail the job.

Wine CPU tests exercise ABI dispatch, lifecycle cleanup, explicit off/on state,
loader decoys, prefix transitions and shared-prefix isolation. Synthetic A1-layout
transitions cover global Winelib removal and active PE32 replacement without an
expiring historical tool artifact. Release audit retains stock/all-builtin and
wine.inf content/timestamp checks.

The W1 gate checks module width and package identity using the same-run fixtures.
Native scope and LUID tests retain their independent boundary checks. CPU gates
do not establish GPU behavior or game compatibility.

## Local commands

Set the explicit profile and provide the pinned Proton checkout. Inside the
locked SDK, supply SOURCE_ROOT, SDK_ROOT, WINE_SDK_ROOT, BUILD_ROOT, DOWNLOAD_ROOT,
DESTDIR and JOBS (1..3), then run `build-components.sh` with BUILD_PART set to
`zluda` or `bridges`. `overlay.py assemble --overlays <directory>` combines the
two verified component archives with the admitted base archive.

`make/zluda-wine-bridge.mk` is the optional full-source build adapter. Include it
from a Proton build with ZLUDA_PACKAGING_ROOT set to this checkout and the same
explicit profile. The adapter uses the selected independent bridge/backend pins.
It does not install or launch a tool on the host.
