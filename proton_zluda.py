"""Launch policy for the bundled ZLUDA backend."""

import hashlib
import json
import os
from pathlib import Path
import tempfile

GE_ARCHIVE_SHA256 = "c5448b76a230384e2d7bc6beb5ccb97bafb7e2c3b6c527cb03a1a546bbcb00a0"
STUB_SHA256 = {
    "x86_64-windows": "0939c5e19c3cce99668cd0f07db99aa2ede810690c37af9c70df273b924fdfd3",
    "i386-windows": "2685d96065ea86c301a3e56f06b61e1e96a22645b7f69e9866721ad2f9f8833f",
}
ARCHES = {"system32": "x86_64-windows", "syswow64": "i386-windows"}


def stub_assets(tool):
    root = Path(tool) / "files/lib/wine-cuda"
    record = json.loads((root / "stubs.json").read_text())
    if record.get("ge_archive_sha256") != GE_ARCHIVE_SHA256:
        raise ValueError("CUDA stubs do not identify pinned GE-Proton11-7")
    if record.get("sha256") != STUB_SHA256:
        raise ValueError("CUDA stub manifest differs from pinned GE hashes")
    for arch in ARCHES.values():
        path = root / "stub" / arch / "nvcuda.dll"
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"][arch]:
            raise ValueError("CUDA stub hash/regular-file check failed: " + str(path))
    return root, record


def dll_override(value, module, mode):
    clauses = []
    for clause in value.split(";"):
        names, separator, setting = clause.partition("=")
        keep = [name for name in names.split(",")
                if name.strip().lower().lstrip("*").removesuffix(".dll") != module]
        if keep and clause:
            clauses.append(",".join(keep) + separator + setting)
    return ";".join([*clauses, module + "=" + mode])


def builtin_override(value):
    return dll_override(value, "nvcuda", "b")


def select_root(env, tool, bootstrap=False, ld_path_var="LD_LIBRARY_PATH", log=None):
    """Select one immutable module namespace for the next Wine process."""
    tool = Path(tool).resolve()
    root, _ = stub_assets(tool)
    on = enabled(env) and not bootstrap
    selected = root / ("zluda" if on else "stub")
    if on:
        for relative in ["x86_64-windows/nvcuda.dll", "i386-windows/nvcuda.dll", "x86_64-unix/nvcuda.so"]:
            if not (selected / relative).is_file():
                raise ValueError("incomplete ZLUDA Wine module root: " + str(selected / relative))
        backend = tool / "files/lib64/libcuda.so.1"
        if not backend.is_file():
            if log is not None:
                log("ZLUDA unavailable: missing bundled backend; continuing with ZLUDA off")
            env["PROTON_ENABLE_ZLUDA"] = "0"
            on = False
            selected = root / "stub"
        else:
            env["ZLUDA_WINE_BACKEND_PATH"] = str(backend)
    if not on:
        env.pop("ZLUDA_WINE_BACKEND_PATH", None)
    paths = []
    for entry in env.get("WINEDLLPATH", "").split(":"):
        if not entry:
            continue
        candidate = Path(entry)
        if any(path.parts[-2:] in (("wine-cuda", "stub"), ("wine-cuda", "zluda"))
               for path in (candidate, candidate.resolve())):
            continue
        paths.append(entry)
    env["WINEDLLPATH"] = ":".join([str(selected), *paths])
    env["WINEDLLOVERRIDES"] = builtin_override(env.get("WINEDLLOVERRIDES", ""))
    # Children can inherit an enabled parent's LD paths. Bootstrap and off must
    # not retain them; the final enabled hook adds them in the established order.
    owned = {tool / "files/lib64", tool / "files/lib64/wine/x86_64-unix",
             tool / "files/lib/wine/x86_64-unix"}
    owned = owned | {path.resolve() for path in owned}
    if ld_path_var in env:
        env[ld_path_var] = ":".join(entry for entry in env[ld_path_var].split(":")
                                  if entry and Path(entry) not in owned and Path(entry).resolve() not in owned)
    return selected


def launch_warnings(env, arguments, log, logfile=None):
    def warn(message):
        log(message)
        if logfile is not None:
            logfile.write(message + "\n")
            logfile.flush()
    flags = [key for key in ("PROTON_NVIDIA_NVCUDA", "PROTON_NVIDIA_LIBS", "PROTON_NVIDIA_NVOPTIX")
             if env.get(key, "0") not in ("", "0")]
    if flags:
        warn("WARNING: " + ", ".join(flags) +
            ": CUDA/NVENC-CUDA/OptiX via nvidia-libs is not supported in this tool")
    if enabled(env) and env.get("WINEARCH") != "wow64" and arguments:
        try:
            with open(arguments[0], "rb") as stream:
                header = stream.read(64)
                if header[:2] == b"MZ":
                    stream.seek(int.from_bytes(header[60:64], "little"))
                    pe = stream.read(6)
                    if pe == b"PE\0\0\x4c\x01":
                        warn("WARNING: ZLUDA cannot service a classic 32-bit game; PROTON_USE_WOW64=1 is required")
        except OSError:
            warn("ZLUDA: executable architecture unknown; 32-bit CUDA requires PROTON_USE_WOW64=1")


def prepare_prefix(proton, compatdata, session, log=None):
    """Under prefix_lock, update builtins using the stub root before game selection."""
    migrate_prefix(proton.base_dir, compatdata.base_dir, log=log)
    timestamp = Path(compatdata.prefix_dir) / ".update-timestamp"
    expected = str(int(os.stat(proton.wine_inf).st_mtime))
    if timestamp.is_file() and timestamp.read_text().strip() == expected:
        return
    env = dict(session.env)
    select_root(env, proton.base_dir, bootstrap=True)
    if session.run_proc([proton.wine_bin, "wineboot", "--update"], env) != 0:
        raise RuntimeError("CUDA stub-root Wineboot update failed")
    if session.run_proc([proton.wineserver_bin, "-w"], env) != 0:
        raise RuntimeError("CUDA stub-root Wineserver wait failed")
    # Wineboot uses the admitted off root itself, rather than repairing its
    # output after the fact. User-supplied native CUDA DLLs remain untouched.
    timestamp.write_text(expected)


def validate_default_prefix(tool, prefix):
    root, record = stub_assets(tool)
    for directory, arch in ARCHES.items():
        path = Path(prefix) / "drive_c/windows" / directory / "nvcuda.dll"
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"][arch]:
            raise ValueError("default_pfx must contain regular GE CUDA stubs: " + str(path))


def materialize_default_prefix(tool, prefix):
    """Only after Wineboot has stopped, under the tool's dist lock."""
    root, _ = stub_assets(tool)
    prefix = Path(prefix).resolve()
    for directory, arch in ARCHES.items():
        path = prefix / "drive_c/windows" / directory / "nvcuda.dll"
        if not path.parent.resolve().is_relative_to(prefix):
            raise ValueError("default prefix CUDA parent leaves template")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.write_bytes((root / "stub" / arch / "nvcuda.dll").read_bytes())
        path.chmod(0o644)
    validate_default_prefix(tool, prefix)


def migrate_prefix(tool, compatdata, log=None):
    """Run once under Proton's prefix lock; preserve native user PE DLLs and unknown files."""
    root, record = stub_assets(tool)
    compatdata = Path(compatdata)
    marker = compatdata / "zluda-cuda-roots-v1.json"
    if marker.exists():
        return
    prefix = (compatdata / "pfx").resolve()
    changes, preserved = [], []
    for directory, arch in ARCHES.items():
        path = prefix / "drive_c/windows" / directory / "nvcuda.dll"
        if not path.parent.resolve().is_relative_to(prefix) or not path.parent.is_dir():
            continue
        managed = False
        if path.is_symlink():
            target = (path.parent / os.readlink(path)).resolve()
            parts = target.parts
            managed = target.name.lower() in ("nvcuda.dll", "nvcuda.dll.so", "nvcuda.so") and (
                any(parts[i:i+3] in (("files", "lib", "wine"), ("files", "lib64", "wine"))
                    for i in range(len(parts)-2)))
        elif path.is_file():
            with path.open("rb") as stream:
                header = stream.read(128)
                managed = header.startswith(b"\x7fELF")
                if header.startswith(b"MZ") and header[0x40:].startswith(
                        (b"Wine builtin DLL", b"Wine placeholder DLL")):
                    pe_offset = int.from_bytes(header[0x3c:0x40], "little")
                    if 0x40 <= pe_offset <= path.stat().st_size - 4:
                        stream.seek(pe_offset)
                        managed = stream.read(4) == b"PE\0\0"
        if managed:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stage:
                stage.write((root / "stub" / arch / "nvcuda.dll").read_bytes())
                temporary = Path(stage.name)
            try:
                temporary.chmod(0o644)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            changes.append(str(path.relative_to(prefix)))
        elif path.exists() or path.is_symlink():
            preserved.append(str(path.relative_to(prefix)))
            if log is not None:
                log("Preserving unrecognized user CUDA DLL: " + str(path))
    marker.write_text(json.dumps({"version": 1, "replaced": changes,
                                  "preserved": preserved}, indent=2) + "\n")



def enabled(env):
    """NVAPI availability alone does not request CUDA translation."""
    return env.get("PROTON_ENABLE_ZLUDA") == "1"


def configure_hardware(env, sys_root=Path("/sys")):
    """Apply the gfx1033 ISA alias only to a verified Steam Deck APU.

    SteamDeck=1 is also used on other hardware. Check DMI and every compute
    device instead; a process-wide ISA override must not spoof an attached GPU.
    Explicit user overrides are preserved.
    """
    if "HSA_OVERRIDE_GFX_VERSION" in env:
        return
    try:
        dmi = sys_root / "class/dmi/id"
        vendor = (dmi / "sys_vendor").read_text().strip()
        product = (dmi / "product_name").read_text().strip()
        board = (dmi / "board_name").read_text().strip()
        if vendor != "Valve" or not {product, board} & {"Jupiter", "Galileo"}:
            return
        targets = []
        for properties in (sys_root / "class/kfd/kfd/topology/nodes").glob("*/properties"):
            target = next((int(line.split()[1]) for line in properties.read_text().splitlines()
                           if line.startswith("gfx_target_version ")), None)
            if target is None:
                return
            if target:
                targets.append(target)
        if targets == [100303]:
            env["HSA_OVERRIDE_GFX_VERSION"] = "10.3.0"
    except (OSError, ValueError, IndexError):
        # Unknown hardware stays native; the user can opt in explicitly.
        return
