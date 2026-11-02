import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os
from proton_zluda import configure_hardware, enabled, force_native_nvcuda, install_wow64_client, is_game_launch


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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        lib = self.root / "u/files/lib"
        (lib / "wine/zluda/i386-windows").mkdir(parents=True)
        (lib / "wine/zluda/i386-windows/nvcuda.dll").write_bytes(b"zluda32")
        (lib / "wine/zluda/zluda64_server.exe").write_bytes(b"server")
        self.lib = str(lib) + "/"
        # Another installed tool (A1), read-only like a packaged tree.
        self.other = self.root / "a1/files/lib/wine/i386-windows"
        self.other.mkdir(parents=True)
        (self.other / "nvcuda.dll").write_bytes(b"a1 relay")
        (self.other / "zluda64_server.exe").write_bytes(b"a1 other")
        self.prefix = self.root / "pfx"
        self.windows = self.prefix / "drive_c/windows"

    def tearDown(self):
        for path in [self.other, *self.other.iterdir()]:
            os.chmod(path, 0o755)
        self.temp.cleanup()

    def plain_prefix(self):
        (self.windows / "syswow64").mkdir(parents=True)

    def freeze_other(self):
        for path in self.other.iterdir():
            os.chmod(path, 0o444)
        os.chmod(self.other, 0o555)

    def assert_other_unchanged(self):
        self.assertEqual((self.other / "nvcuda.dll").read_bytes(), b"a1 relay")
        self.assertEqual((self.other / "zluda64_server.exe").read_bytes(), b"a1 other")
        self.assertEqual(sorted(p.name for p in self.other.iterdir()), ["nvcuda.dll", "zluda64_server.exe"])

    def assert_installed(self):
        for path, data in [(self.windows / "syswow64/nvcuda.dll", b"zluda32"),
                           (self.windows / "zluda64_server.exe", b"server")]:
            self.assertFalse(path.is_symlink())
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(sorted(p.name for p in (self.windows / "syswow64").iterdir()), ["nvcuda.dll"])

    def test_symlink_into_read_only_other_tool(self):
        self.plain_prefix()
        (self.windows / "syswow64/nvcuda.dll").symlink_to(self.other / "nvcuda.dll")
        (self.windows / "zluda64_server.exe").symlink_to(self.other / "zluda64_server.exe")
        self.freeze_other()
        self.assertTrue(install_wow64_client(self.lib, self.prefix))
        self.assert_installed()
        self.assert_other_unchanged()

    def test_hardlink_to_other_tool_file(self):
        self.plain_prefix()
        os.link(self.other / "nvcuda.dll", self.windows / "syswow64/nvcuda.dll")
        os.link(self.other / "zluda64_server.exe", self.windows / "zluda64_server.exe")
        self.assertTrue(install_wow64_client(self.lib, self.prefix))
        self.assert_installed()
        self.assert_other_unchanged()

    def test_hardlink_with_identical_bytes_is_still_replaced(self):
        self.plain_prefix()
        (self.other / "nvcuda.dll").write_bytes(b"zluda32")
        os.link(self.other / "nvcuda.dll", self.windows / "syswow64/nvcuda.dll")
        self.assertTrue(install_wow64_client(self.lib, self.prefix))
        self.assert_installed()
        (self.windows / "syswow64/nvcuda.dll").write_bytes(b"later write")
        self.assertEqual((self.other / "nvcuda.dll").read_bytes(), b"zluda32")

    def test_regular_files_and_repeat(self):
        self.plain_prefix()
        (self.windows / "syswow64/nvcuda.dll").write_bytes(b"GE placeholder")
        (self.windows / "zluda64_server.exe").write_bytes(b"old server")
        self.assertTrue(install_wow64_client(self.lib, self.prefix))
        self.assert_installed()
        inode = (self.windows / "syswow64/nvcuda.dll").stat().st_ino
        self.assertTrue(install_wow64_client(self.lib, self.prefix))
        self.assertEqual((self.windows / "syswow64/nvcuda.dll").stat().st_ino, inode)

    def test_missing_destinations(self):
        self.plain_prefix()
        self.assertTrue(install_wow64_client(self.lib, self.prefix))
        self.assert_installed()

    def test_symlinked_syswow64_directory_is_refused(self):
        # The Deck incident shape: the directory resolves into A1's builtins.
        self.windows.mkdir(parents=True)
        (self.windows / "syswow64").symlink_to(self.other, target_is_directory=True)
        self.freeze_other()
        self.assertFalse(install_wow64_client(self.lib, self.prefix))
        self.assert_other_unchanged()
        self.assertFalse((self.windows / "zluda64_server.exe").exists())

    def test_symlinked_windows_directory_is_refused(self):
        self.prefix.mkdir()
        (self.prefix / "drive_c").mkdir()
        target = self.root / "elsewhere"
        (target / "syswow64").mkdir(parents=True)
        (self.prefix / "drive_c/windows").symlink_to(target, target_is_directory=True)
        self.assertFalse(install_wow64_client(self.lib, self.prefix))
        self.assertEqual(list((target / "syswow64").iterdir()), [])

    def test_symlinked_prefix_root_is_followed_once(self):
        real = self.root / "real-pfx"
        (real / "drive_c/windows/syswow64").mkdir(parents=True)
        self.prefix.symlink_to(real, target_is_directory=True)
        self.assertTrue(install_wow64_client(self.lib, str(self.prefix) + "/"))
        self.assertEqual((real / "drive_c/windows/syswow64/nvcuda.dll").read_bytes(), b"zluda32")

    def test_missing_client_fails_the_launch(self):
        self.plain_prefix()
        (self.root / "u/files/lib/wine/zluda/zluda64_server.exe").unlink()
        with self.assertRaises(FileNotFoundError):
            install_wow64_client(self.lib, self.prefix)


class GameLaunchGate(unittest.TestCase):
    def test_steam_helpers_and_other_verbs_do_not_install(self):
        for argv in (["proton", "run", "C:\\Program Files (x86)\\Steam\\d3ddriverquery64.exe"],
                     ["proton", "run", "/steam/legacycompat/d3ddriverquery64.exe"],
                     ["proton", "waitforexitandrun", "D3DDriverQuery64.EXE"],
                     ["proton", "getcompatpath", "/x/Game.exe"], ["proton", "run"], ["proton"]):
            self.assertFalse(is_game_launch(argv), argv)

    def test_game_launch(self):
        for argv in (["proton", "waitforexitandrun", "/games/Batman/Binaries/ShippingPC-BmGame.exe"],
                     ["proton", "run", "Z:\\tmp\\probe32.exe", "200"]):
            self.assertTrue(is_game_launch(argv), argv)



class NativeOverride(unittest.TestCase):
    def test_replaces_ge_nvcuda_entry_and_keeps_others(self):
        env = {"WINEDLLOVERRIDES": "steam.exe=b;nvapi=n;nvcuda=b;dxgi=n"}
        force_native_nvcuda(env)
        self.assertEqual(env["WINEDLLOVERRIDES"], "steam.exe=b;nvapi=n;dxgi=n;nvcuda=n,b")

    def test_multi_name_entries_and_duplicates(self):
        env = {"WINEDLLOVERRIDES": "nvcuda,nvcuvid=n,b;NVCUDA.dll=d;;winemenubuilder.exe=d"}
        force_native_nvcuda(env)
        self.assertEqual(env["WINEDLLOVERRIDES"], "nvcuvid=n,b;winemenubuilder.exe=d;nvcuda=n,b")

    def test_missing_variable(self):
        env = {}
        force_native_nvcuda(env)
        self.assertEqual(env["WINEDLLOVERRIDES"], "nvcuda=n,b")


if __name__ == "__main__":
    unittest.main()
