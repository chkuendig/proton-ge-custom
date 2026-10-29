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
LEGACY_PREFIX_SHA256 = {
    "42b674644e53a2be4ee46df7fac49f22f42662828dc80f89e2c0c0a9a5ec9898",
    "d67d26c6abcf40c8e90931084d820cff5c761d8e948ee5f8bf17c2a227941759",
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


def nvapi_enabled(config, host_arch):
    return ("disablenvapi" not in config or "forcenvapi" in config) and host_arch != "aarch64-windows"


def prepare_nvapi(env, config):
    """Keep the published one-switch ZLUDA policy ahead of prefix decisions."""
    if enabled(env):
        env["PROTON_FORCE_NVAPI"] = "1"
        env["DXVK_NVAPI_ALLOW_OTHER_DRIVERS"] = "1"
        env["DXVK_ENABLE_NVAPI"] = "1"
        env["DXVK_NVAPI_DRIVER_VERSION"] = "99999"
        env["WINE_HIDE_AMD_GPU"] = "1"
        config.add("forcenvapi")


def select_root(env, tool, bootstrap=False, ld_path_var="LD_LIBRARY_PATH"):
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
            raise ValueError("missing bundled ZLUDA backend: " + str(backend))
        env["ZLUDA_WINE_BACKEND_PATH"] = str(backend)
    else:
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


def validate_default_prefix(tool, prefix):
    root, record = stub_assets(tool)
    for directory, arch in ARCHES.items():
        path = Path(prefix) / "drive_c/windows" / directory / "nvcuda.dll"
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"][arch]:
            raise ValueError("default_pfx must contain regular GE CUDA stubs: " + str(path))


def migrate_prefix(tool, compatdata):
    """Run once under Proton's prefix lock; preserve unknown user DLLs/ELFs."""
    root, record = stub_assets(tool)
    compatdata = Path(compatdata)
    marker = compatdata / "zluda-cuda-roots-v1.json"
    if marker.exists():
        return
    prefix = (compatdata / "pfx").resolve()
    changes = []
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
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            managed = digest in LEGACY_PREFIX_SHA256 or digest in record.get("legacy_prefix_sha256", [])
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
    marker.write_text(json.dumps({"version": 1, "replaced": changes}, indent=2) + "\n")



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
