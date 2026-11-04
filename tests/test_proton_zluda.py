import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proton_zluda


class LaunchPolicy(unittest.TestCase):
    def test_explicit_backend_request(self):
        for env in ({}, {"PROTON_FORCE_NVAPI": "1"}, {"PROTON_ENABLE_ZLUDA": "0"},
                    {"PROTON_ENABLE_ZLUDA": "false"}, {"PROTON_ENABLE_ZLUDA": ""}):
            self.assertFalse(proton_zluda.enabled(env))
        self.assertTrue(proton_zluda.enabled({"PROTON_ENABLE_ZLUDA": "1"}))

    def hardware(self, gpus, env=None, error=None):
        from unittest.mock import patch
        env = dict(env if env is not None else {'PROTON_ENABLE_ZLUDA': '1'})
        utilities = SimpleNamespace(get_vulkan_gpus=Mock(return_value=gpus, side_effect=error))
        with patch.dict(sys.modules, {'utilities': utilities}):
            proton_zluda.configure_device(env)
        return env

    def test_deck_variants(self):
        for name in ("AMD Custom GPU 0405 (RADV VANGOGH)",
                     "AMD Custom GPU 0932 (RADV vangogh)"):
            gpu = SimpleNamespace(vendorID=0x1002, deviceName=name)
            self.assertEqual(self.hardware([gpu])["HSA_OVERRIDE_GFX_VERSION"], "10.3.0")

    def test_other_or_unavailable_gpus(self):
        for gpus in ([], [SimpleNamespace(vendorID=0x1002, deviceName="AMD Radeon")],
                     [SimpleNamespace(vendorID=0x10de, deviceName="VANGOGH")]):
            self.assertNotIn("HSA_OVERRIDE_GFX_VERSION", self.hardware(gpus))

    def test_explicit_override_and_disabled_zluda(self):
        gpu = SimpleNamespace(vendorID=0x1002, deviceName="VANGOGH")
        self.assertEqual(self.hardware([gpu], {"PROTON_ENABLE_ZLUDA": "1",
                                               "HSA_OVERRIDE_GFX_VERSION": "11.0.0"})
                         ["HSA_OVERRIDE_GFX_VERSION"], "11.0.0")
        self.assertNotIn("HSA_OVERRIDE_GFX_VERSION", self.hardware([gpu], {}))

    def test_enumeration_failure_does_not_override_or_crash(self):
        for error in (RuntimeError("enumeration failed"), OSError("driver unavailable"),
                      ValueError("unsupported property")):
            self.assertNotIn("HSA_OVERRIDE_GFX_VERSION", self.hardware([], error=error))

    def test_family_match_is_not_disabled_by_another_gpu(self):
        gpus = [SimpleNamespace(vendorID=0x10de, deviceName="Other GPU"),
                SimpleNamespace(vendorID=0x1002, deviceName="RADV VANGOGH")]
        self.assertEqual(self.hardware(gpus)["HSA_OVERRIDE_GFX_VERSION"], "10.3.0")

    def test_ge_helper_failure_discards_partial_devices_and_caches(self):
        from functools import cache
        tree = ast.parse((Path(__file__).resolve().parents[1] / "utilities.py").read_text())
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == "get_vulkan_gpus")
        class VulkanError(RuntimeError):
            pass
        class Instance:
            creates = 0
            def __enter__(self):
                type(self).creates += 1
                return self
            def __exit__(self, *args):
                return False
            def enumerate_physical_devices(self):
                yield SimpleNamespace(vendorID=0x1002, deviceName="VANGOGH")
                raise VulkanError("failed after first device")
        namespace = {"cache": cache, "GPU": SimpleNamespace(from_physical_device=lambda x: x),
                     "VulkanInstance": Instance, "VulkanError": VulkanError}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])),
                     "utilities.py", "exec"), namespace)
        function = namespace["get_vulkan_gpus"]
        self.assertEqual(function(), [])
        self.assertEqual(function(), [])
        self.assertEqual(Instance.creates, 1)


if __name__ == "__main__":
    unittest.main()
