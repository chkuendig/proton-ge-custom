import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from proton_zluda import configure_hardware, enabled


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


if __name__ == "__main__":
    unittest.main()
