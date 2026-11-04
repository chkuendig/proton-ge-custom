# Valve Proton ZLUDA qualification build

This feature lane ports the published B5 overlay to Valve stable Proton
`proton-11.0-2e` (`bc896217`). It uses the normal `proton` entry point.
It is a qualification candidate, with no release or default-tool change.

Run `.github/workflows/zluda-overlay.yml` manually on the feature branch with
`profile=overlay`, `historical_a1=true` while the historical artifact exists,
and an empty `components_run`. The full tool and Wine build execute only in
the digest-pinned Steam Runtime SDK on hosted GitHub runners, with three jobs.
No Deck access or installation is part of this workflow.

`inputs.lock.json` pins the full Valve source, native B5 ZLUDA/LLVM/ROCm,
DXVK 60193094, and dxvk-nvapi fd105c0d plus the two production patches. The
independent `wine-bridge` gitlink is resolved solely through `bridge_input.py`
and the `wine_bridge` lock section. Its own `BACKEND-SOURCE.json` supplies
six digest-admitted backend declaration inputs. The bridge component can
move repositories or directories without teaching consumers a ZLUDA layout.

The bridge uses an explicit GE/Valve provider set. Valve's stock exit ordering
has a separate reviewer contract. The complete PE64/PE32/ELF64 triad is rebuilt
against the new SDK. These bridge binaries are **sources/protocol-matched to
B5, not identical B5 bits**. The native backend and ROCm comparison remain
checksum-matched to published B5; the release audit rejects a mismatch.

`build-wine.sh` builds the complete unmodified Valve distribution first and
captures it as `valve-stock.tar.gz`. Built distribution timestamps are
normalized to the pinned Valve source epoch. It then applies only the
Mafia II `leave_handler` fix and rebuilds with the same upstream recipe.
All builtins and `wine.inf` bytes and timestamps are audited against that
stock build, published B5 and historical A1. The normal Valve media stack
is retained. The GE Wine/staging stack and optional nvidia-libs are absent.

Assembly creates separate immutable stub and ZLUDA namespaces, validates
all three modules and the exact stock stub hashes, and keeps template
placeholders as regular files. Bootstrap and installers select stock stubs.
Enabled launches select the bundled backend and NVAPI policy. Prefix copies
use B5's exclusive temporary files and atomic replacement, and isolate
foreign builtin links before Wine updates a shared prefix.

Title policy replaces the protonfixes framework for 17410, 208650, 35140,
50130 and 49520. B5's early defaults and explicit `PROTON_ENABLE_ZLUDA=0`
are preserved. PE32 enabled titles select WoW64 before Wine initialization.
BL2 substitutes the executable argument and adds `-NoSplash`. Runtime
prerequisites use the pinned winetricks **script**, executed locally with
stock CUDA stubs. No installer or NVIDIA redistributable bytes are included
in build artifacts. Mirror retains B5's disclosed game-local file moves;
clean-files support and final activation policy remain owner decisions.

Hosted acceptance includes native scope/PCI tests, real PE32 LUID tests,
12 ABI cases, three lifecycle modes, real-launcher mode/decoy transitions,
historical A1 plus durable layout stand-ins, extracted-source offline rebuild
of all three bridge targets, and loader/manifest/source/license audits.
NVAPI qualification extends only a separate test-source copy to cover both
flag values on NVIDIA/RADV mocks, V1, V2 and poisoned/null output arrays on
both architectures. The production NVAPI tree retains exactly two patches.

CPU results establish host functionality only. Game/media/GPU correctness,
performance, NVIDIA hardware behavior and owner adoption require their own
stages and approvals. Artifacts are uploaded for review, never published as
GitHub releases by this workflow.
