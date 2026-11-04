#!/usr/bin/env bash
# Full Valve distribution, then the one diagnosed Wine change. Hosted SDK only.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DOWNLOAD_ROOT:?}" "${WINE_SDK_ROOT:?}"
[[ ${CI:-} == true ]] || { echo 'Run through the hosted workflow' >&2; exit 2; }
jobs=${JOBS:-3}
[[ $jobs =~ ^[1-3]$ ]]
here=$SOURCE_ROOT/packaging/zluda-overlay
out=$BUILD_ROOT/wine
stock=$out/src/proton
mkdir -p "$out/log" "$stock"
exec > >(tee -a "$out/log/build-wine.log") 2>&1
pin=$(python3 -c 'import json,os; print(json.load(open(os.environ["SOURCE_ROOT"]+"/packaging/zluda-overlay/inputs.lock.json"))["proton_base"])')
git -C "$stock" init -q
git -C "$stock" fetch --depth=1 https://github.com/ValveSoftware/Proton.git "$pin" refs/tags/proton-11.0-2e
git -C "$stock" checkout -q --detach "$pin"
git -C "$stock" tag proton-11.0-2e "$pin"
git -C "$stock" submodule update --init --recursive --depth=1 --filter=blob:none --jobs=3
python3 "$here/valve_tool.py" admit "$stock" --output "$out/log/valve-source-pins.json"
python3 "$here/inputs.py" fetch "$DOWNLOAD_ROOT" --group valve
mkdir -p "$stock/contrib"
cp "$DOWNLOAD_ROOT/wine-mono-11.2.0-x86.tar.xz" "$DOWNLOAD_ROOT/xalia-0.4.9-net48-mono.zip" "$stock/contrib/"
epoch=$(git -C "$stock" show -s --format=%ct HEAD)
cd "$out"
# This invokes Valve's normal full distribution target inside the already
# locked hosted SDK. It never invokes configure's container-probing launcher.
make -f "$stock/Makefile.in" SRCDIR="$stock" CONTAINER=1 \
    BUILD_NAME=Valve-Proton11.0-2e-stock INTERNAL_TOOL_NAME=Valve-Proton11.0-2e-stock \
    SOURCE_DATE_EPOCH="$epoch" BASE_SOURCE_DATE_EPOCH="$epoch" -j"$jobs" dist
# Normalize the locally built stock distribution to its pinned source epoch.
# The same captured bytes/mtimes are used as the candidate assembly baseline.
python3 "$here/valve_tool.py" normalize "$out/dist" --epoch "$epoch" --output "$out/log/stock-mtimes.json"
# Capture the actual unmodified stock tool, including every builtin/mtime.
tar --sort=name --owner=0 --group=0 --numeric-owner -C "$out/dist" -cf - . \
    | gzip -n > /work/valve-stock.tar.gz
python3 "$here/valve_tool.py" stock "$out/dist" --archive /work/valve-stock.tar.gz \
    --output /work/VALVE-STOCK.json
# Rebuild through the same upstream recipe, changing only the Mafia II line.
patch -d "$stock/wine" -p1 < "$SOURCE_ROOT/patches/wine-hotfixes/pending/ntdll-restore-thread-state-after-steamclient-fault-redirect.patch"
make -f "$stock/Makefile.in" SRCDIR="$stock" CONTAINER=1 \
    BUILD_NAME=Valve-Proton11.0-2e-stock INTERNAL_TOOL_NAME=Valve-Proton11.0-2e-stock \
    SOURCE_DATE_EPOCH="$epoch" BASE_SOURCE_DATE_EPOCH="$epoch" -j"$jobs" wine
# Give the rsynced, configured source its real upstream lineage before the
# reviewer signs it; generated source files remain in this prepared snapshot.
git -C "$out/src-wine" init -q
git -C "$out/src-wine" fetch --depth=1 "$stock/wine" HEAD
git -C "$out/src-wine" reset --mixed FETCH_HEAD
git -C "$out/src-wine" add -A -f -- . ':(exclude)autom4te.cache' ':(exclude,glob)**/*.orig'
git -C "$out/src-wine" commit -q -m 'Prepare Valve Wine SDK with Mafia II handler restoration' \
    -m 'Assisted-by: Codex:gpt-6.1-sol'
python3 "$here/wine_sdk.py" "$out" "$WINE_SDK_ROOT" --generated \
    --object "$out/obj-wine-x86_64" --source "$out/src-wine"
mkdir -p "$out/overlay/files/lib/wine/x86_64-unix"
install -m755 "$out/dist/files/lib/wine/x86_64-unix/ntdll.so" \
    "$out/overlay/files/lib/wine/x86_64-unix/ntdll.so"
cp /work/VALVE-STOCK.json "$out/overlay/VALVE-STOCK.json"
python3 "$here/valve_tool.py" delta "$out/overlay" --stock /work/valve-stock.tar.gz \
    --output "$out/overlay/WINE-SCOPE.json"
(cd "$out/overlay" && sha256sum files/lib/wine/x86_64-unix/ntdll.so > FILES.sha256)
python3 "$here/valve_tool.py" sources "$stock" --prepared-wine "$out/src-wine" \
    --output "$out/valve-proton-sources.tar.gz"
