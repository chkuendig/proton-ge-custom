"""Launch policy for the bundled ZLUDA backend."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

TITLE_DEPENDENCIES = {
    '17410': ('d3dx9', 'd3dcompiler_43', 'physx'),
    '208650': ('physx',),
    '35140': ('d3dx9', 'd3dcompiler_43', 'physx'),
    '50130': ('d3dx9',),
    '49520': ('physx',),
}
PE32_TITLES = {'17410', '35140', '50130', '49520'}


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


def title_id(env):
    return env.get('SteamAppId', env.get('SteamGameId', ''))


def early_title_policy(env, verb):
    """Apply settings before Session captures environment and selects WINEARCH."""
    if verb not in ('run', 'waitforexitandrun'):
        return
    appid = title_id(env)
    if appid in TITLE_DEPENDENCIES:
        env.setdefault('PROTON_ENABLE_ZLUDA', game_setting())
        if enabled(env) and appid in PE32_TITLES:
            env['PROTON_USE_WOW64'] = '1'
    if enabled(env):
        env['PROTON_FORCE_NVAPI'] = '1'
        env['DXVK_NVAPI_ALLOW_OTHER_DRIVERS'] = '1'
        env['DXVK_ENABLE_NVAPI'] = '1'
    if appid == '49520':
        env['PROTON_NO_ESYNC'] = '1'


def late_title_policy(session, proton, compatdata, argv, ld_path_var, log):
    """Run local prerequisites with stock CUDA stubs after prefix setup."""
    if argv[1] not in ('run', 'waitforexitandrun'):
        return
    appid = title_id(session.env)
    verbs = TITLE_DEPENDENCIES.get(appid, ())
    if not verbs:
        return
    env = dict(session.env)
    select_root(env, proton.base_dir, bootstrap=True, ld_path_var=ld_path_var)
    env.update(WINEPREFIX=compatdata.prefix_dir, WINE=proton.wine_bin,
               WINELOADER=proton.wine_bin, WINESERVER=proton.wineserver_bin,
               WINETRICKS_LATEST_VERSION_CHECK='disabled', LD_PRELOAD='')
    installed = Path(compatdata.prefix_dir) / 'winetricks.log'
    with compatdata.prefix_lock:
        for dependency in verbs:
            if installed.is_file() and dependency in installed.read_text().splitlines():
                continue
            subprocess.run([proton.wineserver_bin, '-w'], env=env, check=True)
            script = str(Path(proton.base_dir) / 'files/bin/winetricks')
            subprocess.run(['bash', script, '--unattended', dependency], env=env, check=True)
            subprocess.run([proton.wineserver_bin, '-w'], env=env, check=True)
            argv[1] = 'waitforexitandrun'
    if appid == '17410':
        binaries = Path(session.env['STEAM_COMPAT_INSTALL_PATH']) / 'Binaries'
        for name in ('PhysXCore.dll', 'PhysXDevice.dll', 'PhysXLocal'):
            bundled, moved = binaries / name, binaries / (name + '.bak')
            if not bundled.exists():
                continue
            if moved.is_dir():
                shutil.rmtree(moved)
            elif moved.exists():
                moved.unlink()
            bundled.rename(moved)
            log('Moved bundled ' + name + ' aside for the system PhysX runtime')
    if appid == '49520' and len(argv) > 2:
        argv[2] = argv[2].replace('Launcher.exe', 'Borderlands2.exe')
        if '-NoSplash' not in argv[3:]:
            argv.append('-NoSplash')


def configure_device(env):
    if not enabled(env) or 'HSA_OVERRIDE_GFX_VERSION' in env:
        return
    try:
        dmi = Path('/sys/class/dmi/id')
        vendor, product, board = [(dmi / name).read_text().strip()
                                  for name in ('sys_vendor', 'product_name', 'board_name')]
        if vendor != 'Valve' or not any(model in ('Jupiter', 'Galileo') for model in (product, board)):
            return
        targets = []
        for node in Path('/sys/class/kfd/kfd/topology/nodes').iterdir():
            values = [line.split() for line in (node / 'properties').read_text().splitlines()
                      if line.split() and line.split()[0] == 'gfx_target_version']
            if len(values) != 1 or len(values[0]) != 2:
                return
            value = values[0][1]
            if not value.isascii() or not value.removeprefix('+').isdecimal() or int(value) > 0xffffffff:
                return
            if int(value):
                targets.append(int(value))
        if targets == [100303]:
            env['HSA_OVERRIDE_GFX_VERSION'] = '10.3.0'
    except (OSError, ValueError, IndexError):
        pass


VALVE_PROTON_PIN = "bc896217ffcd67f892f952b010f898fe7bd0f0a1"
ARCHES = {"system32": "x86_64-windows", "syswow64": "i386-windows"}


def stub_assets(tool):
    root = Path(tool) / "files/lib/wine-cuda"
    record = json.loads((root / "stubs.json").read_text())
    stock = json.loads((Path(tool) / "provenance/VALVE-STOCK.json").read_text())
    if stock.get('proton_pin') != VALVE_PROTON_PIN or record.get('proton_pin') != VALVE_PROTON_PIN:
        raise ValueError('CUDA stubs do not identify the pinned Valve build')
    if record.get('sha256') != stock.get('nvcuda_sha256') or set(record.get('sha256', {})) != set(ARCHES.values()):
        raise ValueError('CUDA stubs differ from the full Valve stock build')
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
    """Wineboot/installers always use stock Valve stubs, before the final launch choice."""
    if proton.host_pe_arch != "aarch64-windows":
        select_root(session.env, proton.base_dir, bootstrap=True, ld_path_var=ld_path_var)
    if proton.missing_default_prefix():
        proton.make_default_prefix()
    if proton.host_pe_arch != "aarch64-windows":
        with proton.dist_lock:
            materialize_default_prefix(proton.base_dir, proton.default_pfx_dir)


def prepare_cuda_prefix(session, proton, compatdata, log=None):
    """Reset CUDA placeholders under prefix_lock, including stock off launches."""
    env = dict(session.env)
    select_root(env, proton.base_dir, log=log)
    session.env['PROTON_ENABLE_ZLUDA'] = env.get('PROTON_ENABLE_ZLUDA', '0')
    for directory in ARCHES:
        path = Path(compatdata.prefix_dir) / 'drive_c/windows' / directory / 'nvcuda.dll'
        source = Path(proton.default_pfx_dir) / 'drive_c/windows' / directory / 'nvcuda.dll'
        atomic_placeholder(source, path)
        path.with_suffix('.dll.debug').unlink(missing_ok=True)
    return enabled(env)


def atomic_placeholder(source, destination):
    descriptor, name = tempfile.mkstemp(prefix='.' + destination.name + '.', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output, open(source, 'rb') as original:
            shutil.copyfileobj(original, output)
            os.fchmod(output.fileno(), 0o644)
        os.replace(name, destination)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def validate_default_prefix(tool, prefix):
    """Canonical stubs cannot coexist with per-launch roots; template copies can."""
    root, record = stub_assets(tool)
    for directory, arch in ARCHES.items():
        path = Path(prefix) / "drive_c/windows" / directory / "nvcuda.dll"
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"][arch]:
            raise ValueError("default_pfx must contain regular Valve CUDA stubs: " + str(path))


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
        atomic_placeholder(root / 'stub' / arch / 'nvcuda.dll', path)
    validate_default_prefix(tool, prefix)


def configure_launch(session, proton, arguments, ld_path_var, log, bootstrap=False):
    """Final game selection; helper launches retain the stock stub namespace."""
    if proton.host_pe_arch == "aarch64-windows":
        return
    env = session.env
    select_root(env, proton.base_dir, bootstrap=bootstrap, ld_path_var=ld_path_var, log=log)
    launch_warnings(env, arguments, log, session.log_file)
    if enabled(env) and not bootstrap:
        lib = str(Path(proton.base_dir) / 'files/lib64')
        env[ld_path_var] = lib + '/wine/x86_64-unix:' + lib + ':' + env.get(ld_path_var, '')
        env['PROTON_FORCE_NVAPI'] = '1'
        env['DXVK_NVAPI_ALLOW_OTHER_DRIVERS'] = '1'
        env['DXVK_ENABLE_NVAPI'] = '1'
        configure_device(env)
        log('ZLUDA enabled: CUDA-on-HIP translation active')


def enabled(env):
    """NVAPI availability alone does not request CUDA translation."""
    return env.get("PROTON_ENABLE_ZLUDA") == "1"
