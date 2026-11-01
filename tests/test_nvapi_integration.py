"""Clean NVAPI source/test admission and false-success rejection, without Wine."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import nvapi_package as package
import nvapi_cpu as cpu
from inputs import sha256


class NvapiIntegration(unittest.TestCase):
    def test_graphics_imports_reject_the_other_architecture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(cpu.cuda_package, 'pe_info', return_value={'machine':0x8664}):
                with self.assertRaisesRegex(ValueError, 'architecture mismatch'):
                    cpu.stage_graphics(root / 'tool', root / 'prefix', 'wow64')

    def test_pe32_adapter_changes_only_private_test_build_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            (source / 'tests').mkdir(parents=True)
            (source / 'src').mkdir()
            (source / 'src/production.cpp').write_text('exact production bytes')
            (source / 'src/interfaces').mkdir()
            (source / 'src/interfaces/vkd3d-proton_interfaces.h').write_text(
                'virtual HRESULT CreateResourceFromBorrowedHandle(int, int, int) = 0;\n')
            (source / 'meson.build').write_text("if enable_tests and dxvk_cpu_family == 'x86_64' # compiling tests for x86 fails, dunno why...\n  subdir('tests')\nendif\n")
            (source / 'tests/meson.build').write_text("nvapi_src = files([])\nnvapi_tests_src = files(['unrelated.cpp'])\ntarget_name = 'nvapi'+target_suffix+'-tests'\n")
            (source / 'tests/mocks').mkdir()
            for name in ['dxgi_mocks.h', 'd3d12_mocks.h']:
                (source / 'tests/mocks' / name).write_text('MAKE_MOCK2(QueryInterface, sig, override);\nIMPLEMENT_MOCK0(AddRef);\nIMPLEMENT_MOCK3(CreateResourceFromBorrowedHandle);\n#if defined(_MSC_VER)\n#endif\n')
            dest, receipt = root / 'private-test-source', root / 'adapter.json'
            package.adapt_pe32(source, dest, receipt)
            self.assertEqual((source / 'src/production.cpp').read_bytes(), (dest / 'src/production.cpp').read_bytes())
            self.assertIn('nvapi_sysinfo.cpp', (dest / 'tests/meson.build').read_text())
            self.assertIn('nvapi_sysinfo_cuda.cpp', (dest / 'tests/meson.build').read_text())
            self.assertIn('nvapi_sysinfo_topo.cpp', (dest / 'tests/meson.build').read_text())
            self.assertEqual(set(json.loads(receipt.read_text())['changes']),
                             {'meson.build', 'tests/meson.build', 'tests/mocks/dxgi_mocks.h', 'tests/mocks/d3d12_mocks.h'})
            self.assertIn('MAKE_STDMETHOD_MOCK2', (dest / 'tests/mocks/dxgi_mocks.h').read_text())
            self.assertIn('MAKE_MOCK2', (source / 'tests/mocks/dxgi_mocks.h').read_text())
            self.assertIn('IMPLEMENT_MOCK3(CreateResourceFromBorrowedHandle)',
                          (dest / 'tests/mocks/d3d12_mocks.h').read_text())
            self.assertNotIn('!defined(_WIN64)', (dest / 'tests/mocks/d3d12_mocks.h').read_text())
            self.assertIn("dxvk_cpu_family == 'x86_64'", (source / 'meson.build').read_text())

    def test_zero_exit_or_no_matching_tests_cannot_be_called_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'test.log'
            for text in ['No test cases matched filter', 'All tests passed (0 assertions in 0 test cases)', 'exit zero']:
                log.write_text(text)
                with self.assertRaises(ValueError):cpu.result(log)
            log.write_text('All tests passed (5 assertions in 1 test case)')
            self.assertEqual(cpu.result(log)['assertions'], 5)

    def test_fixture_receipt_must_match_the_actual_assembled_nvapi_dlls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures, tool = root / 'fixtures', root / 'tool'
            fixtures.mkdir()
            (fixtures / 'PE32-ADAPTER.json').write_text('{}')
            tests = {}
            for name in ['nvapi-tests.exe', 'nvapi64-tests.exe']:
                (fixtures / name).write_text(name)
                tests[name] = sha256(fixtures / name)
            binaries = {}
            for name in package.BINARIES:
                path = tool / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fresh NVAPI')
                binaries[name] = sha256(path)
            record = {'proton':'a'*40, 'input_lock_sha256':sha256(cpu.HERE / 'inputs.lock.json'),
                      'sdk_image':cpu.LOCK['sdk_image'], 'patches':cpu.LOCK['component_patches']['dxvk-nvapi'],
                      'pe32_adapter_sha256':sha256(fixtures / 'PE32-ADAPTER.json'), 'tests':tests, 'binaries':binaries}
            (fixtures / 'NVAPI-TESTS.json').write_text(json.dumps(record))
            with mock.patch.object(cpu, 'verify_manifest'), mock.patch.object(cpu, 'git', return_value='a'*40):
                cpu.admit(root, tool, fixtures)
                (tool / package.BINARIES[2]).write_text('old shipping NVAPI')
                with self.assertRaisesRegex(ValueError, 'assembled DLLs'):cpu.admit(root, tool, fixtures)
