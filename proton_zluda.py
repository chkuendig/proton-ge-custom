"""Launch policy for the bundled ZLUDA backend."""

import os
from pathlib import Path


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


def install_wow64_client(lib_dir, prefix_dir):
    """Reinstall upstream's 32-bit CUDA client and its server in the prefix.

    GE runs WoW64 without 32-bit nvidia-libs and resets syswow64/nvcuda.dll
    to the default-prefix placeholder on every launch. zluda32 starts
    zluda64_server.exe from its module directory's parent, C:\\windows.
    """
    windows = Path(prefix_dir) / "drive_c/windows"
    for source, target in [(Path(lib_dir) / "wine/i386-windows/nvcuda.dll", windows / "syswow64/nvcuda.dll"),
                           (Path(lib_dir) / "wine/zluda/zluda64_server.exe", windows / "zluda64_server.exe")]:
        data = source.read_bytes()
        if not target.is_symlink() and target.is_file() and target.read_bytes() == data:
            continue
        partial = target.with_name(target.name + ".zluda-partial")
        partial.write_bytes(data)
        os.replace(partial, target)
