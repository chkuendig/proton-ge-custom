import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proton_zluda import configure_hardware, enabled, install_wow64_client


class LaunchPolicy(unittest.TestCase):
    def test_explicit_backend_request(self):
        for env in ({}, {"PROTON_FORCE_NVAPI": "1"}, {"PROTON_ENABLE_ZLUDA": "0"},
                    {"PROTON_ENABLE_ZLUDA": "false"}, {"PROTON_ENABLE_ZLUDA": ""}):
            self.assertFalse(enabled(env))
        self.assertTrue(enabled({"PROTON_ENABLE_ZLUDA": "1"}))

    def hardware(self, vendor="Valve", model="Galileo", targets=(0, 100303), override=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dmi = root / "class/dmi/id"
            dmi.mkdir(parents=True)
            for key, value in {"sys_vendor": vendor, "product_name": model, "board_name": model}.items():
                (dmi / key).write_text(value + "\n")
            for index, target in enumerate(targets):
                node = root / f"class/kfd/kfd/topology/nodes/{index}"
                node.mkdir(parents=True)
                (node / "properties").write_text(f"gfx_target_version {target}\n")
            env = {"SteamDeck": "1"}
            if override is not None:
                env["HSA_OVERRIDE_GFX_VERSION"] = override
            configure_hardware(env, root)
            return env.get("HSA_OVERRIDE_GFX_VERSION")

    def test_both_deck_models(self):
        for model in ("Jupiter", "Galileo"):
            self.assertEqual(self.hardware(model=model), "10.3.0")

    def test_other_hardware_and_missing_device_stay_native(self):
        self.assertIsNone(self.hardware(vendor="ASUSTeK COMPUTER INC.", model="ROG Ally"))
        self.assertIsNone(self.hardware(model="Other Valve device"))
        self.assertIsNone(self.hardware(targets=(0, 100304)))
        self.assertIsNone(self.hardware(targets=(0,)))

    def test_attached_gpu_stays_native(self):
        self.assertIsNone(self.hardware(targets=(0, 100303, 110000)))

    def test_explicit_override_survives(self):
        self.assertEqual(self.hardware(override="11.0.0"), "11.0.0")

    def test_unreadable_identity_stays_native(self):
        env = {"SteamDeck": "1"}
        with tempfile.TemporaryDirectory() as temporary:
            configure_hardware(env, Path(temporary))
        self.assertNotIn("HSA_OVERRIDE_GFX_VERSION", env)



class Wow64Client(unittest.TestCase):
    def tool(self, root):
        lib = root / "files/lib"
        (lib / "wine/i386-windows").mkdir(parents=True)
        (lib / "wine/zluda").mkdir(parents=True)
        (lib / "wine/i386-windows/nvcuda.dll").write_bytes(b"zluda32")
        (lib / "wine/zluda/zluda64_server.exe").write_bytes(b"server")
        prefix = root / "pfx"
        (prefix / "drive_c/windows/syswow64").mkdir(parents=True)
        return str(lib) + "/", str(prefix) + "/"

    def test_replaces_ge_placeholder_and_installs_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lib, prefix = self.tool(root)
            windows = root / "pfx/drive_c/windows"
            # GE restores the default-prefix copy on every launch.
            (windows / "syswow64/nvcuda.dll").write_bytes(b"placeholder")
            install_wow64_client(lib, prefix)
            self.assertEqual((windows / "syswow64/nvcuda.dll").read_bytes(), b"zluda32")
            self.assertEqual((windows / "zluda64_server.exe").read_bytes(), b"server")
            self.assertEqual(sorted(p.name for p in windows.iterdir()), ["syswow64", "zluda64_server.exe"])

    def test_replaces_links_and_stale_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lib, prefix = self.tool(root)
            windows = root / "pfx/drive_c/windows"
            other = root / "other.dll"
            other.write_bytes(b"zluda32")
            (windows / "syswow64/nvcuda.dll").symlink_to(other)
            (windows / "zluda64_server.exe").write_bytes(b"old server")
            install_wow64_client(lib, prefix)
            self.assertFalse((windows / "syswow64/nvcuda.dll").is_symlink())
            self.assertEqual((windows / "syswow64/nvcuda.dll").read_bytes(), b"zluda32")
            self.assertEqual((windows / "zluda64_server.exe").read_bytes(), b"server")
            self.assertEqual(other.read_bytes(), b"zluda32")

    def test_missing_client_fails_the_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lib, prefix = self.tool(root)
            (root / "files/lib/wine/zluda/zluda64_server.exe").unlink()
            with self.assertRaises(FileNotFoundError):
                install_wow64_client(lib, prefix)


if __name__ == "__main__":
    unittest.main()
