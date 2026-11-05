"""Launch policy for the bundled ZLUDA backend."""

import hashlib
import json
import os
from pathlib import Path
import shutil

GE_ARCHIVE_SHA256 = "c5448b76a230384e2d7bc6beb5ccb97bafb7e2c3b6c527cb03a1a546bbcb00a0"
STUB_SHA256 = {
    "x86_64-windows": "0939c5e19c3cce99668cd0f07db99aa2ede810690c37af9c70df273b924fdfd3",
    "i386-windows": "2685d96065ea86c301a3e56f06b61e1e96a22645b7f69e9866721ad2f9f8833f",
}
ARCHES = {"system32": "x86_64-windows", "syswow64": "i386-windows"}


def game_setting():
    """Explicit settings take precedence over the AMD game default."""
    value = os.environ.get("PROTON_ENABLE_ZLUDA")
    if value is None:
        try:
            import user_settings
            value = user_settings.user_settings.get("PROTON_ENABLE_ZLUDA")
        except Exception:
            pass
    if value is not None:
        return str(value)
    try:
        vendors = {int((device / "vendor").read_text(), 16)
                   for device in Path("/sys/bus/pci/devices").iterdir()
                   if int((device / "class").read_text(), 16) >> 16 == 0x03}
        return "1" if 0x1002 in vendors and 0x10de not in vendors else "0"
    except (OSError, ValueError):
        return "0"


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
    if on:
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
    if enabled(env) and flags:
        warn("WARNING: " + ", ".join(flags) +
             ": ZLUDA is active; skipping GE nvidia-libs nvcuda copy and nvcuda=n,b")
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


def bootstrap(session, proton, ld_path_var):
    """Wineboot/installers always use GE stubs, before the final launch choice."""
    if proton.host_pe_arch != "aarch64-windows":
        select_root(session.env, proton.base_dir, bootstrap=True, ld_path_var=ld_path_var)
    if proton.missing_default_prefix():
        proton.make_default_prefix()
    if proton.host_pe_arch != "aarch64-windows":
        with proton.dist_lock:
            materialize_default_prefix(proton.base_dir, proton.default_pfx_dir)


def prepare_cuda_prefix(session, proton, compatdata, log=None):
    """Select CUDA under prefix_lock and reset native copies for enabled launches."""
    env = dict(session.env)
    select_root(env, proton.base_dir, log=log)
    session.env['PROTON_ENABLE_ZLUDA'] = env.get('PROTON_ENABLE_ZLUDA', '0')
    if not enabled(env):
        return False
    for directory in ARCHES:
        path = Path(compatdata.prefix_dir) / 'drive_c/windows' / directory / 'nvcuda.dll'
        source = Path(proton.default_pfx_dir) / 'drive_c/windows' / directory / 'nvcuda.dll'
        path.unlink(missing_ok=True)
        shutil.copyfile(source, path)
        path.with_suffix('.dll.debug').unlink(missing_ok=True)
    return True


def validate_default_prefix(tool, prefix):
    """Canonical stubs cannot coexist with per-launch roots; template copies can."""
    root, record = stub_assets(tool)
    for directory, arch in ARCHES.items():
        path = Path(prefix) / "drive_c/windows" / directory / "nvcuda.dll"
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"][arch]:
            raise ValueError("default_pfx must contain regular GE CUDA stubs: " + str(path))


def materialize_default_prefix(tool, prefix):
    """After Wineboot has stopped, under Proton's dist lock (or during packaging)."""
    root, record = stub_assets(tool)
    prefix = Path(prefix).resolve()
    for directory, arch in ARCHES.items():
        path = prefix / 'drive_c/windows' / directory / 'nvcuda.dll'
        if not path.parent.resolve().is_relative_to(prefix):
            raise ValueError('default prefix CUDA parent leaves template')
        if not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256'][arch]:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        path.write_bytes((root / 'stub' / arch / 'nvcuda.dll').read_bytes())
        path.chmod(0o644)
    validate_default_prefix(tool, prefix)


def configure_launch(session, proton, arguments, ld_path_var, log):
    """Select final CUDA and NVAPI settings after protonfixes."""
    if proton.host_pe_arch == "aarch64-windows":
        return
    env = session.env
    select_root(env, proton.base_dir, ld_path_var=ld_path_var, log=log)
    launch_warnings(env, arguments, log, session.log_file)
    if enabled(env):
        lib = str(Path(proton.base_dir) / 'files/lib64')
        env[ld_path_var] = lib + '/wine/x86_64-unix:' + lib + ':' + env.get(ld_path_var, '')
        env['PROTON_FORCE_NVAPI'] = '1'
        env['DXVK_NVAPI_ALLOW_OTHER_DRIVERS'] = '1'
        env['DXVK_ENABLE_NVAPI'] = '1'
        log('ZLUDA enabled: CUDA-on-HIP translation active')


def enabled(env):
    """NVAPI availability alone does not request CUDA translation."""
    return env.get("PROTON_ENABLE_ZLUDA") == "1"
