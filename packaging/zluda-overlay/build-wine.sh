#!/usr/bin/env bash
# Adapted from the public hub build-wine.sh at 67e7788a; module selection,
# GE ELF dependency/version checks and source preparation are retained.
# Execute in the locked Steam Runtime SDK, never on the installation host.
set -euo pipefail
export GIT_AUTHOR_NAME='Christian Kündig' GIT_COMMITTER_NAME='Christian Kündig'
export GIT_AUTHOR_EMAIL=christian@kuendig.info GIT_COMMITTER_EMAIL=christian@kuendig.info
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}" "${DOWNLOAD_ROOT:?}" "${WINE_SDK_ROOT:?}"
repo=$SOURCE_ROOT out=$BUILD_ROOT/wine
here=$(cd -- "$(dirname -- "$0")" && pwd)
jobs=${JOBS:-3}
[[ $jobs =~ ^[1-3]$ ]] || { echo 'JOBS must be 1..3' >&2; exit 2; }
cache=$DOWNLOAD_ROOT
HOST_GLIBC=2.39
full=0 rev=HEAD
[[ $# == 0 ]] || { echo 'no arguments; use explicit environment inputs' >&2; exit 2; }
mkdir -p "$out/log"
exec > >(tee -a "$out/log/build-wine.log") 2>&1
python3 "$here/bridge_tools.py" "$repo" "$out/log/bridge-tools-system.json" --generated
for tool in git patch python3 autoreconf bison flex perl gcc x86_64-w64-mingw32-gcc i686-w64-mingw32-gcc objcopy; do
    command -v "$tool" >/dev/null
done
[[ $(gcc -m32 -print-file-name=libc.so) == /* ]]
rev=$(git -C "$repo" rev-parse --verify "$rev^{commit}")
# Tags are optional in CI clones. Use the same exact GE source/archive pins as
# final assembly, never a nearest-tag guess or a descriptive SHA as a filename.
base_inputs=$(python3 "$here/inputs.py" wine-base "$repo")
read -r base base_name ge_archive <<< "$base_inputs"
src=$out/src tgz=$cache/$ge_archive
[[ -s $tgz ]] || { echo "missing $tgz" >&2; exit 1; }
mkdir -p "$src" "$out/stamps"

stamped() { [[ -f $out/stamps/$1 && $(<"$out/stamps/$1") == "$2" ]]; }
stamp() { printf '%s\n' "$2" > "$out/stamps/$1"; }
prepsh() { git -C "$repo" show "$1:patches/protonprep-valve-staging.sh"; }
# protonprep's helper functions and its Wine section.
wine_section() {
    local pp; pp=$(prepsh "$1")
    sed -n '1,/^### (1) PREP SECTION ###/p' <<< "$pp" | sed '$d'
    sed -n '/^### (2) WINE PATCHING ###/,/^### END WINE PATCHING ###/p' <<< "$pp"
}
# Everything the prepared Wine tree is made from; install-zluda-tool.sh
# computes the same hash to match an overlay to the line it installs.
wine_inputs() {
    local r=$1 d
    {
        wine_section "$r"
        git -C "$repo" rev-parse "$r:wine" "$r:wine-staging" "$r:wineopenxr"
        git -C "$repo" rev-parse -q --verify "$r:patches/wineopenxr" || true
        wine_section "$r" | grep -o '\.\./patches/[^/" ]*' | sort -u | while read -r d; do
            git -C "$repo" rev-parse "$r:patches/${d#../patches/}"
        done
    } | sha256sum | cut -c1-16
}
url() { git -C "$repo" config --blob "$1:.gitmodules" "submodule.$2.url"; }
fetch() {  # dir url commit [depth]
    mkdir -p "$1"
    [[ -d $1/.git ]] || git init -q "$1"
    git -C "$1" cat-file -e "$3^{commit}" 2>/dev/null || git -C "$1" fetch -q --depth "${4:-1}" "$2" "$3"
}

prep() {  # name rev -> tag of the prepared tree in $src/wine
    local name=$1 r=$2 tag log wp sp c
    tag=prep/$1-$(git -C "$repo" rev-parse --short=12 "$2^{commit}")
    if git -C "$src/wine" rev-parse -q --verify "refs/tags/$tag" >/dev/null 2>&1; then
        echo "$tag"; return
    fi
    log=$out/log/prep-$name.log
    wp=$(git -C "$repo" rev-parse "$r:wine") sp=$(git -C "$repo" rev-parse "$r:wine-staging")
    fetch "$src/wine" "$(url "$r" wine)" "$wp" >&2
    # protonprep reverts commits ("git revert --no-commit <sha>"): fetch them with their parent.
    for c in $(wine_section "$r" | grep -o 'git revert --no-commit [0-9a-f]\{40\}' | awk '{print $4}'); do
        fetch "$src/wine" "$(url "$r" wine)" "$c" 2 >&2
    done
    fetch "$src/wine-staging" "$(url "$r" wine-staging)" "$sp" >&2
    git -C "$src/wine-staging" checkout -q -f --detach "$sp"; git -C "$src/wine-staging" clean -qxdf
    git -C "$src/wine" checkout -q -f --detach "$wp"; git -C "$src/wine" clean -qxdf
    rm -rf "$src/patches" "$src/wineopenxr"
    git -C "$repo" archive "$r" patches wineopenxr | tar -x -C "$src"
    wine_section "$r" > "$out/log/prep-$name.sh"
    echo ":: preparing $name ($r), log $log" >&2
    (
        shopt -s nullglob
        cd "$src/wineopenxr"   # protonprep's PREP section, before Wine copies it
        for p in ../patches/wineopenxr/*.patch; do patch -Np1 < "$p"; done
        cd "$src" && bash "$out/log/prep-$name.sh"
        # GE's Makefile: .wine-post-source, autoreconf, rules-wine-requests
        cd "$src/wine"
        XDG_CACHE_HOME=$out/cache dlls/winevulkan/make_vulkan -x vk.xml -X video.xml
        tools/make_specfiles
        autoreconf -fi
        tools/make_requests
    ) > "$log" 2>&1 || { echo "prep $name failed, see $log" >&2; exit 1; }
    if grep -nE 'FAILED|Skipping patch|can.t find file to patch|malformed|garbage|Traceback|^ERROR|[Ee]rror:|No such file' "$log" >&2; then
        echo "prep $name: patch errors above, see $log" >&2; exit 1
    fi
    git -C "$src/wine" add -A -f -- . ':(exclude)autom4te.cache' ':(exclude,glob)**/*.orig'
    git -C "$src/wine" -c user.name="Christian Kündig" -c user.email=christian@kuendig.info \
        commit -q --no-verify --allow-empty -m "$tag" -m "Assisted-by: Codex:gpt-6.1-sol"
    git -C "$src/wine" tag -f "$tag" >/dev/null
    echo "$tag"
}

# Prep resets and repatches the scratch checkout. Preserve the timestamps of
# clean, unchanged files so changing one patch does not rebuild all of Wine.
source_mtimes() {
    python3 - "$1" "$src/wine" "$out/source-mtimes.json" <<'PY'
import json, os, pathlib, subprocess, sys
mode, source, snapshot = sys.argv[1:]
source = pathlib.Path(source)
def git(*args):
    return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL)
try:
    tree = git('ls-tree', '-rz', 'HEAD')
except subprocess.CalledProcessError:
    if mode != 'save':
        raise
    tree = b''
if mode == 'save':
    dirty = set(os.fsdecode(p) for p in git('diff', '--name-only', '-z', 'HEAD', '--').split(b'\0')) if tree else set()
    records = {}
else:
    records = json.loads(pathlib.Path(snapshot).read_text())
reused = 0
for entry in tree.split(b'\0'):
    if not entry:
        continue
    metadata, path = entry.split(b'\t', 1)
    file_mode, kind, oid = metadata.decode().split()
    if kind != 'blob':
        continue
    path = os.fsdecode(path)
    file = source / path
    if mode == 'save':
        if path not in dirty:
            st = file.lstat()
            records[path] = [file_mode, oid, st.st_atime_ns, st.st_mtime_ns]
    elif path in records and records[path][:2] == [file_mode, oid]:
        os.utime(file, ns=tuple(records[path][2:]), follow_symlinks=False)
        reused += 1
if mode == 'save':
    pathlib.Path(snapshot).write_text(json.dumps(records))
else:
    print(f':: preserved mtimes of {reused} unchanged source files')
PY
}
source_mtimes save
tag_base=$(prep base "$base")
tag_ours=$(prep ours "$rev")
git -C "$src/wine" checkout -q -f --detach "$tag_ours"
source_mtimes restore
git -C "$src/wine" diff --name-only "$tag_base" "$tag_ours" > "$out/changed-sources.txt"
echo ":: $(wc -l < "$out/changed-sources.txt") source files differ from $base:"
sed 's/^/   /' "$out/changed-sources.txt"
if [[ ! -s $out/changed-sources.txt ]]; then
    echo "$rev prepares the same Wine as $base: nothing to build"
    rm -rf "$out/overlay"; exit 0
fi
wine_scope=$(python3 "$here/wine_scope.py" "$src/wine" "$tag_base" "$tag_ours" "$out/wine-scope.json")
scope_args=()
[[ $wine_scope != x86_64-unix-ntdll ]] || scope_args=(--native-only)

# Reuse Proton's complete prerequisite graph and exact per-arch Wine flags.
# The focused Makefile drops unrelated factories; dependency recipes themselves
# and their source/configure/build/post-build ordering remain GE's.
make_tool=$(command -v make)
ninja_tool=$(command -v ninja)
python3 "$here/wine_deps.py" "$repo" "$out" --make-tool "$make_tool" --ninja-tool "$ninja_tool" --jobs "$jobs" "${scope_args[@]}"
export PATH="$out/proton-tools:$PATH"
export CMAKE_BUILD_PARALLEL_LEVEL=$jobs CARGO_BUILD_JOBS=$jobs
build_dependencies=$(dpkg-query -W | LC_ALL=C sort | sha256sum | cut -d' ' -f1)
build_modules=()
if (( ! full )); then
    while read -r path; do
        case $path in
            dlls/*/*|programs/*/*) build_modules+=("$(cut -d/ -f1-2 <<< "$path")") ;;
            server/*) build_modules+=(server) ;;
            *) echo "not module-local: $path; rerun with --full" >&2; exit 1 ;;
        esac
    done < "$out/changed-sources.txt"
    mapfile -t build_modules < <(printf '%s\n' "${build_modules[@]}" | sort -u)
fi
printf '%s\n' "${build_modules[@]}" > "$out/build-modules.txt"
wine_profile=$wine_scope
(( ! full )) || wine_profile=all
build() {
    local arch=$1
    "$make_tool" -C "$out/proton-deps" -f "$out/proton-wine.mk" -j1 \
        "MAKE=$out/proton-tools/make" "overlay-wine-$arch" \
        "OVERLAY_WINE_PROFILE=$wine_profile" "OVERLAY_WINE_MODULES=$out/build-modules.txt" \
        > "$out/log/make-$arch.log" 2>&1 || { tail -40 "$out/log/make-$arch.log" >&2; exit 1; }
}
build x86_64
if [[ $wine_scope != x86_64-unix-ntdll ]]; then build i386; fi

# --- overlay -----------------------------------------------------------------
# The release's regular files (top directory stripped) and their sizes.
python3 - "$here" "$tgz" "$out/ge-files.txt" "$out/ge-sizes.txt" "$out/wine-scope.json" <<'PY'
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
import wine_scope
wine_scope.inventory(*map(Path, sys.argv[2:]))
PY
ours() {  # GE release path -> our built module, or installed file for --full
    local p=$1 f=${1##*/} arch subdir m candidate
    if (( ! full )); then
        case $p in
            files/bin/wineserver) echo "$out/obj-x86_64/server/wineserver"; return ;;
            files/lib/wine/i386-*) arch=i386 ;;
            files/lib/wine/x86_64-*) arch=x86_64 ;;
            *) return 1 ;;
        esac
        subdir=''
        [[ $p != *-windows/* ]] || subdir=$arch-windows/
        for m in "${build_modules[@]}"; do
            candidate=$out/obj-$arch/$m/$subdir$f
            if [[ -f $candidate ]]; then echo "$candidate"; return; fi
        done
        return 1
    fi
    case $p in
        files/lib/wine/i386-unix/wine64*) echo "$out/dst-x86_64/lib/wine/x86_64-unix/$f" ;;
        files/lib/wine/i386-*) echo "$out/dst-i386/${p#files/}" ;;
        files/lib/wine/x86_64-*|files/share/wine/*|files/bin/*) echo "$out/dst-x86_64/${p#files/}" ;;
        *) return 1 ;;
    esac
}
module_files() {  # dlls/<m> or programs/<m> -> GE release paths
    local m=${1#*/} names
    names=("$m" "$m.dll" "$m.exe" "$m.so" "${m%.*}.so")
    printf '%s\n' "${names[@]}" | sort -u | while read -r n; do
        grep -E "^files/lib/wine/(i386|x86_64)-(unix|windows)/${n//./\\.}\$" "$out/ge-files.txt" || true
    done
}
wide=() mods=() sel=()
while read -r path; do
    case $path in
        dlls/*/*|programs/*/*) mods+=("$(cut -d/ -f1-2 <<< "$path")") ;;
        server/*) sel+=(files/bin/wineserver) ;;
        *) wide+=("$path") ;;
    esac
done < "$out/changed-sources.txt"
for m in $(printf '%s\n' "${mods[@]}" | sort -u); do
    mapfile -t f < <(module_files "$m")
    if (( ${#f[@]} )); then sel+=("${f[@]}"); else wide+=("$m (no file of its own in the release)"); fi
done
mode=modules
if (( ${#wide[@]} )); then
    if (( ! full )); then
        echo "not module-local, rerun with --full to overlay all of Wine:" >&2
        printf '   %s\n' "${wide[@]}" >&2; exit 1
    fi
fi
if (( full )); then
    mode=full sel=()
    while read -r p; do
        o=$(ours "$p") || continue
        [[ -e $o ]] && sel+=("$p")
    done < <(grep -E '^files/(lib/wine/(i386|x86_64)-(unix|windows)/|bin/(wine|wineserver|msidb)$|share/wine/)' "$out/ge-files.txt")
    echo ":: --full: GE Wine files our build lacks (left as GE's):"
    grep -E '^files/lib/wine/(i386|x86_64)-(unix|windows)/' "$out/ge-files.txt" | while read -r p; do
        o=$(ours "$p"); [[ -e $o ]] || echo "   $p"
    done
fi
if [[ $wine_scope == x86_64-unix-ntdll ]]; then
    # This exact prepared-source hunk compiles only into the native Unix ntdll.
    # Keep both PE ntdlls and classic i386 ntdll.so from the locked GE archive.
    sel=(files/lib/wine/x86_64-unix/ntdll.so)
fi

ovl=$out/overlay
rm -rf "$ovl.partial"; mkdir -p "$ovl.partial"
glibc_max=none missing=0
for p in $(printf '%s\n' "${sel[@]}" | sort -u); do
    o=$(ours "$p") || { echo "our build lacks $p" >&2; missing=1; continue; }
    [[ -e $o ]] || { echo "our build lacks $p" >&2; missing=1; continue; }
    install -D -m "$(stat -c %a "$o")" "$o" "$ovl.partial/$p"
    t=$ovl.partial/$p
    case $(file -b "$t") in
        ELF*)
            objcopy -p --strip-debug "$t"
            v=$(objdump -T "$t" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1 || true)
            if [[ -n $v ]]; then
                [[ $glibc_max == none ]] || v=$(printf '%s\n' "$glibc_max" "$v" | sort -V | tail -1)
                glibc_max=$v
            fi ;;
        PE32+*) x86_64-w64-mingw32-objcopy -p --strip-debug "$t" ;;
        PE32*) i686-w64-mingw32-objcopy -p --strip-debug "$t" ;;
    esac
    if [[ $p == *-windows/* ]] && ! grep -q 'Wine builtin DLL' "$t"; then
        echo "$p: no builtin marker" >&2; missing=1
    fi
done
(( ! missing )) || exit 1
# A host-compatible glibc version alone does not prove Steam can load an
# overlay: optional distrobox libraries can add unavailable dependencies.
python3 - "$tgz" "$ovl.partial" <<'PY'
import os, pathlib, re, subprocess, sys, tarfile, tempfile
archive, overlay = sys.argv[1], pathlib.Path(sys.argv[2])
readelf_env = dict(os.environ, LC_ALL='C')
def readelf(file, option):
    return subprocess.check_output(['readelf', option, str(file)], text=True, env=readelf_env)
def needed(file):
    dynamic = readelf(file, '-d')
    if not re.search(r'^Dynamic section at offset .* contains \d+ entries:', dynamic, re.M):
        sys.exit(f'{file}: ELF has no readable dynamic section')
    return sorted(set(re.findall(r'\(NEEDED\).*Shared library: \[([^]]+)\]', dynamic)))
def versions(file):
    info = readelf(file, '--version-info')
    needs = info.split('Version needs section', 1)
    result, library = {}, None
    if len(needs) == 2:
        for line in needs[1].splitlines():
            entry = re.search(r'File: (\S+)', line)
            if entry:
                library = entry[1]
                result.setdefault(library, set())
            version = re.search(r'Name: (\S+)', line)
            if version:
                if library is None:
                    sys.exit(f'{file}: version requirement without a library')
                result[library].add(version[1])
        if not result or any(not values for values in result.values()):
            sys.exit(f'{file}: could not parse version needs')
    return {library: sorted(values) for library, values in sorted(result.items())}
def version_rank(version):
    match = re.fullmatch(r'([^\d]*)(\d+(?:[._]\d+)*)', version)
    if not match:
        return None
    numbers = list(map(int, re.split(r'[._]', match[2])))
    while numbers and numbers[-1] == 0:
        numbers.pop()
    return match[1], tuple(numbers)
def version_allowed(version, baseline):
    if version in baseline:
        return True
    rank = version_rank(version)
    return rank is not None and any(
        other is not None and rank[0] == other[0] and rank[1] <= other[1]
        for other in map(version_rank, baseline))
report = []
with tarfile.open(archive) as tf, tempfile.TemporaryDirectory(prefix='wine-ge-needed-') as tmp:
    members = {m.name.split('/', 1)[-1]: m for m in tf if m.isfile()}
    for file in sorted((overlay / 'files').rglob('*')):
        if not file.is_file():
            continue
        with file.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        path = file.relative_to(overlay).as_posix()
        if path not in members:
            sys.exit(f'{path}: no GE release counterpart')
        original = pathlib.Path(tmp) / 'ge.elf'
        original.write_bytes(tf.extractfile(members[path]).read())
        ge, ours = needed(original), needed(file)
        ge_versions, our_versions = versions(original), versions(file)
        report.extend([path, f'GE NEEDED={ge}', f'ours NEEDED={ours}',
                       f'GE version needs={ge_versions}',
                       f'ours version needs={our_versions}', ''])
        if ours != ge:
            sys.exit(f'{path}: NEEDED differs from GE: ours={ours}, GE={ge}')
        for library, requirements in our_versions.items():
            for version in requirements:
                if not version_allowed(version, ge_versions.get(library, [])):
                    sys.exit(f'{path}: {library} version {version} exceeds GE requirements '
                             f'{ge_versions.get(library, [])}')
        print(f':: dependencies and version needs match GE: {path}: {", ".join(ours) or "none"}')
(overlay / 'ELF-DEPENDENCIES').write_text('\n'.join(report))
PY
if [[ $glibc_max != none ]] && [[ $(printf '%s\n' "GLIBC_$HOST_GLIBC" "$glibc_max" | sort -V | tail -1) != "GLIBC_$HOST_GLIBC" ]]; then
    echo "overlay needs $glibc_max, the target runtime supports $HOST_GLIBC" >&2; exit 1
fi
(cd "$ovl.partial" && find files -type f | sort | xargs sha256sum) > "$ovl.partial/FILES.sha256"
{
    echo "rev=$rev"
    echo "base=$base_name $base $(sha256sum < "$tgz" | cut -c1-64)"
    echo "inputs=$(wine_inputs "$rev")"
    echo "base_inputs=$(wine_inputs "$base")"
    echo "wine=$(git -C "$repo" rev-parse "$rev:wine") staging=$(git -C "$repo" rev-parse "$rev:wine-staging")"
    echo "built=$(date -Is) gcc=$(gcc -dumpfullversion) mingw=$(x86_64-w64-mingw32-gcc -dumpfullversion) glibc_build=$(ldd --version | awk 'NR == 1 { print $NF }')"
    echo "build_dependencies=$build_dependencies"
    echo "configure_recipe_sha256=$(sha256sum "$out/proton-wine.mk" | cut -d' ' -f1)"
    echo "mode=$mode glibc_max=$glibc_max host_glibc=$HOST_GLIBC"
    echo '# changed sources'
    sed 's/^/  /' "$out/changed-sources.txt"
    echo '# files: ours / GE size'
    for p in $(cd "$ovl.partial" && find files -type f | sort); do
        echo "  $p $(stat -c %s "$ovl.partial/$p") / $(awk -F'\t' -v p="$p" '$1 == p { print $2 }' "$out/ge-sizes.txt")"
    done
} > "$ovl.partial/OVERLAY"
rm -rf "$ovl"; mv "$ovl.partial" "$ovl"
cat "$ovl/OVERLAY"
echo "== done $(date -Is): $ovl"

# Expose both Unix and PE SDK archives plus the exact prepared headers/tools.
python3 "$here/wine_sdk.py" "$out" "$WINE_SDK_ROOT" --generated
