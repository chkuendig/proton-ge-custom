"""Launch policy for the bundled ZLUDA backend."""

import os
from pathlib import Path
import stat
import tempfile


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


# Steam runs these under every installed tool, outside any game.
STEAM_HELPERS = {"d3ddriverquery64.exe", "d3ddriverquery.exe"}


def is_game_launch(argv):
    """Only a real app launch installs the 32-bit client, never a Steam helper."""
    if len(argv) < 3 or argv[1] not in ("run", "waitforexitandrun"):
        return False
    target = argv[2].replace("\\", "/").rsplit("/", 1)[-1].lower()
    return target not in STEAM_HELPERS


def _install(source, target, directory):
    """Swap `target`'s directory entry for a new file; never write through links."""
    data = source.read_bytes()
    try:
        st = os.lstat(target)
        if stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and target.read_bytes() == data:
            return
    except FileNotFoundError:
        pass
    fd, partial = tempfile.mkstemp(dir=directory, prefix="." + target.name + ".")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(partial, 0o755)
        os.replace(partial, target)
    except BaseException:
        if os.path.lexists(partial):
            os.unlink(partial)
        raise
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def install_wow64_client(lib_dir, prefix_dir):
    """Reinstall upstream's 32-bit CUDA client and its server in the prefix.

    zluda32 is not a Wine builtin: the tool ships it outside Wine's DLL
    path, and it is installed as a native DLL only into ZLUDA game prefixes
    (pair it with `force_native_nvcuda`). GE resets syswow64/nvcuda.dll on
    every launch. zluda32 starts zluda64_server.exe from its module
    directory's parent, C:\\windows.

    Each destination directory must resolve to the prefix's own directory.
    A symlinked windows or syswow64 directory could point into another
    tool's lib/wine tree, so such a prefix is refused and left untouched.
    Returns whether both files are installed.
    """
    prefix = Path(os.path.realpath(prefix_dir))
    windows = prefix / "drive_c/windows"
    pairs = [(Path(lib_dir) / "wine/zluda/i386-windows/nvcuda.dll", windows / "syswow64", "nvcuda.dll"),
             (Path(lib_dir) / "wine/zluda/zluda64_server.exe", windows, "zluda64_server.exe")]
    for _, directory, _ in pairs:
        if os.path.realpath(directory) != str(directory) or not directory.is_dir():
            return False
    for source, directory, name in pairs:
        _install(source, directory / name, directory)
    return True


def force_native_nvcuda(env):
    """Load the prefix's native zluda32 first: nvcuda=n,b for this launch.

    GE serializes its override list into WINEDLLOVERRIDES before the ZLUDA
    hook runs, with nvcuda=b when NVAPI is on. Drop every existing nvcuda
    entry (Wine's choice among duplicate keys is not something to rely on),
    keep all other entries, and append ours.
    """
    kept = []
    for entry in env.get("WINEDLLOVERRIDES", "").split(";"):
        if not entry.strip():
            continue
        names, sep, value = entry.partition("=")
        rest = [n for n in names.split(",") if n.strip().lower() not in ("nvcuda", "nvcuda.dll")]
        if rest:
            kept.append(",".join(rest) + sep + value)
    env["WINEDLLOVERRIDES"] = ";".join(kept + ["nvcuda=n,b"])
