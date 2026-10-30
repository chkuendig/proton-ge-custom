"""Contract and namespace fixtures; no bridge is executed or qualified here."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import tarfile
import io
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from cuda_binary import pe, elf
import cuda_package as package
import proton_zluda as policy
import wine_sdk


class CudaPackage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = self.root / 'tool'
        self.artifacts = self.root / 'artifacts'
        self.commit = 'a' * 40
        hashes = {}
        for arch, machine in [('x86_64-windows', 0x8664), ('i386-windows', 0x14c)]:
            path = self.tool / 'files/lib/wine' / arch / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.write_bytes(pe(machine))
            hashes[arch] = package.digest(path)
        patch = mock.patch.object(policy, 'STUB_SHA256', hashes)
        patch.start()
        self.addCleanup(patch.stop)
        for directory, arch in policy.ARCHES.items():
            path = self.tool / 'files/share/default_pfx/drive_c/windows' / directory / 'nvcuda.dll'
            path.parent.mkdir(parents=True)
            path.symlink_to('../../../../../lib/wine/' + arch + '/nvcuda.dll')
        for name in package.MODULES:
            path = self.artifacts / name
            path.parent.mkdir(parents=True)
            path.write_bytes(elf() if name.endswith('.so') else
                             pe(0x8664, 788) if name.startswith('x86_64') else pe(0x14c, 690))
        (self.artifacts / 'BUILD-CONTRACT.json').write_text(json.dumps({
            'contract': 'zluda-wine-packaging-1', 'source_commit': self.commit,
            'rust': 'rustc 1.99.0 (fixture)', 'modules': package.MODULES,
            'qualification': 'PENDING: fixtures only'}))
        self.checksums()

    def checksums(self):
        (self.artifacts / 'SHA256SUMS').write_text(''.join(
            package.digest(self.artifacts / name) + '  ' + name + '\n' for name in package.MODULES))

    def install(self):
        package.prepare_stubs(self.tool)
        package.install_bridge(self.artifacts, self.tool, self.commit)
        package.remove_canonical(self.tool)
        (self.tool / 'files/lib64').mkdir(exist_ok=True)
        (self.tool / 'files/lib64/wine').symlink_to('../lib/wine')
        package.audit(self.tool)

    def test_exact_roots_and_regular_default_stubs_without_canonical_or_alias(self):
        canonical = self.tool / 'files/lib/wine'
        for name in ['x86_64-unix/nvcuda.dll.so', 'x86_64-unix/nvcuda.so', 'x86_64-unix/libcuda.so', 'x86_64-unix/libcuda.so.1',
                     'nvidia-libs/nvcuda/i386-windows/nvcuda.dll.so']:
            path = canonical / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'legacy')
        self.install()
        for name in package.MODULES:
            self.assertEqual((self.tool / 'files/lib/wine-cuda/zluda' / name).read_bytes(),
                             (self.artifacts / name).read_bytes())
        self.assertFalse(any(p.name in package.CANONICAL_NAMES for p in canonical.rglob('*')))
        policy.validate_default_prefix(self.tool, self.tool / 'files/share/default_pfx')
        prov = json.loads((self.tool / 'provenance/ZLUDA-WINE.json').read_text())
        self.assertIn('PENDING', prov['qualification'])
        for name in ['files/lib/wine-cuda/stub/x86_64-unix/nvcuda.so',
                     'files/lib/wine-cuda/stub/x86_64-windows/libcuda.so.1',
                     'files/lib/wine/x86_64-unix/nvcuda.so', 'files/lib/wine/x86_64-unix/libcuda.so.1']:
            path = self.tool / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'unexpected')
            with self.assertRaises(ValueError):
                package.audit(self.tool)
            path.unlink()
        stub = self.tool / 'files/lib/wine-cuda/stub/i386-windows/nvcuda.dll'
        stub.write_bytes(b'wrong GE stub')
        with self.assertRaisesRegex(ValueError, 'stub hash'):
            package.audit(self.tool)

    def test_contract_checksum_module_and_architecture_fail_closed(self):
        for mode in ['commit', 'checksum', 'missing', 'machine', 'exports', 'wow64', 'gpu-dependency', 'symlink']:
            with self.subTest(mode=mode):
                path = self.artifacts / 'i386-windows/nvcuda.dll'
                original = path.read_bytes()
                native = self.artifacts / 'x86_64-unix/nvcuda.so'
                saved_native = native.read_bytes()
                if mode == 'missing':
                    path.unlink()
                elif mode == 'machine':
                    path.write_bytes(pe(0x8664, 690))
                elif mode == 'exports':
                    path.write_bytes(pe(0x14c, 689))
                elif mode == 'wow64':
                    native.write_bytes(elf(wow64=False))
                elif mode == 'gpu-dependency':
                    native.write_bytes(elf(needed=['libcuda.so.1']))
                elif mode == 'symlink':
                    path.unlink()
                    path.symlink_to(self.artifacts / 'x86_64-windows/nvcuda.dll')
                if mode not in ['missing', 'checksum']:
                    self.checksums()
                if mode == 'checksum':
                    path.write_bytes(b'corrupt')
                with self.assertRaises((ValueError, FileNotFoundError)):
                    package.install_bridge(self.artifacts, self.tool, 'b' * 40 if mode == 'commit' else self.commit)
                path.unlink(missing_ok=True)
                path.write_bytes(original)
                native.write_bytes(saved_native)
                self.checksums()

    def test_x64_draft_inspection_is_separate_from_complete_package_contract(self):
        draft = self.root / 'draft'
        draft.mkdir()
        (draft / 'nvcuda.dll').write_bytes(pe(0x8664, 788))
        (draft / 'nvcuda.so').write_bytes(elf(wow64=False))
        def checksums():
            (draft / 'SHA256SUMS').write_text(''.join(package.digest(draft / name) + '  ' + name + '\n'
                                                   for name in ['nvcuda.dll', 'nvcuda.so']))
        checksums()
        record = package.inspect_draft(draft, self.commit)
        self.assertIn('UNQUALIFIED', record['qualification'])
        self.assertIn('NOT RUN', record['full_packaging_gate'])
        self.assertEqual(record['unix']['call_tables'], ['__wine_unix_call_funcs'])
        with self.assertRaisesRegex(ValueError, 'native/WoW64'):
            package.elf_info(draft / 'nvcuda.so')  # full packaging stays fail-closed
        self.assertFalse((self.tool / 'files/lib/wine-cuda/zluda').exists())
        (draft / 'nvcuda.dll').write_bytes(pe(0x8664, 787))
        checksums()
        self.assertEqual(package.inspect_draft(draft, self.commit)['x64_export_count'], 'review-required')
        (draft / 'nvcuda.so').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            package.inspect_draft(draft, self.commit)

    def test_ci_draft_route_cannot_package_overlay_or_run_wrong_part(self):
        source = self.root / 'source'
        scripts = source / 'packaging/zluda-overlay'
        scripts.mkdir(parents=True)
        trace = self.root / 'trace'
        (scripts / 'build-draft.sh').write_text('echo draft >> "$TRACE"\n')
        (scripts / 'build-components.sh').write_text('echo overlay >> "$TRACE"\nexit 91\n')
        tools = self.root / 'tools'
        tools.mkdir()
        for name, text in [('git', 'exit 0'), ('df', "printf 'Avail\\n40000000000\\n'")]:
            path = tools / name
            path.write_text('#!/bin/sh\n' + text + '\n')
            path.chmod(0o755)
        env = dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], TRACE=str(trace),
                   SOURCE_ROOT=str(source), BUILD_ROOT=str(self.root / 'build'),
                   DOWNLOAD_ROOT=str(self.root / 'downloads'), SDK_ROOT='/unused',
                   WINE_SDK_ROOT='/unused', DESTDIR=str(self.tool))
        for profile, part, expected, lines in [('x64-draft', 'bridges', 0, ['draft']),
                  ('x64-draft', 'zluda', 2, []), ('invalid', 'bridges', 2, []),
                  ('overlay', 'bridges', 91, ['overlay'])]:
            trace.unlink(missing_ok=True)
            result = subprocess.run(['bash', str(ROOT / 'packaging/zluda-overlay/ci-build.sh')],
                       env=dict(env, BUILD_PROFILE=profile, BUILD_PART=part),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(trace.read_text().splitlines() if trace.exists() else [], lines)
            self.assertFalse((self.tool / 'MANIFEST').exists())

    def test_pe_import_policy_rejects_unbundled_compiler_runtimes_on_both_arches(self):
        allowed = ['KERNEL32.dll', 'msvcrt.dll', 'ntdll.dll', 'USERENV.dll',
                   'WS2_32.dll', 'api-ms-win-core-synch-l1-2-0.dll', 'bcryptprimitives.dll',
                   'dbghelp.dll', 'advapi32.dll', 'ucrtbase.dll', 'USER32.dll']
        for machine in [0x14c, 0x8664]:
            path = self.root / 'dependency.dll'
            path.write_bytes(pe(machine, imports=allowed))
            info = package.pe_info(path)
            self.assertEqual(info['imports'], allowed)
            package.check_pe_imports(info)
            for name in ['libgcc_s_seh-1.dll', 'libgcc_s_dw2-1.dll', 'libstdc++-6.dll',
                         'libwinpthread-1.dll', 'nvcuda.dll', '../kernel32.dll',
                         'api-ms-win-core/evil.dll', r'api-ms-win-core\evil.dll',
                         'api-ms-win-core:evil.dll', 'ext-ms-win-core/evil.dll',
                         r'ext-ms-win-core\evil.dll', 'ext-ms-win-core:evil.dll',
                         'api-ms-win-core_fake.dll']:
                for delayed in [False, True]:
                    path.write_bytes(pe(machine, imports=allowed if delayed else [*allowed, name],
                                        delay_imports=[name] if delayed else []))
                    with self.assertRaisesRegex(ValueError, 'delayed import directory' if delayed else
                                                'unexpected PE bridge DLL dependency'):
                        package.check_pe_imports(package.pe_info(path))

    def test_import_violation_blocks_install_final_audit_and_compile_draft(self):
        bad = self.artifacts / 'i386-windows/nvcuda.dll'
        bad.write_bytes(pe(0x14c, 690, imports=['libgcc_s_dw2-1.dll']))
        self.checksums()
        with self.assertRaisesRegex(ValueError, 'libgcc_s_dw2-1.dll'):
            package.install_bridge(self.artifacts, self.tool, self.commit)
        bad.write_bytes(pe(0x14c, 690))
        self.checksums()
        self.install()
        final = self.tool / 'files/lib/wine-cuda/zluda/x86_64-windows/nvcuda.dll'
        final.write_bytes(pe(0x8664, 788, delay_imports=['libwinpthread-1.dll']))
        with self.assertRaisesRegex(ValueError, 'delayed import directory'):
            package.audit(self.tool)
        draft = self.root / 'bad-draft'
        draft.mkdir()
        (draft / 'nvcuda.dll').write_bytes(pe(0x8664, 788, imports=['libgcc_s_seh-1.dll']))
        (draft / 'nvcuda.so').write_bytes(elf(wow64=False))
        (draft / 'SHA256SUMS').write_text(''.join(package.digest(draft / name) + '  ' + name + '\n'
                                               for name in ['nvcuda.dll', 'nvcuda.so']))
        with self.assertRaisesRegex(ValueError, 'libgcc_s_seh-1.dll'):
            package.inspect_draft(draft, self.commit)

    def test_malformed_pe_import_table_does_not_hide_dependencies(self):
        import struct
        path = self.root / 'malformed.dll'
        data = bytearray(pe(0x8664, 788, imports=['kernel32.dll']))
        directory = 152 + 112
        # Truncate the declared directory before its terminating descriptor.
        struct.pack_into('<I', data, directory + 8 + 4, 20)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'unterminated PE import descriptor'):
            package.pe_info(path)
        # Point a DLL name into virtual memory outside file-backed bytes.
        data = bytearray(pe(0x8664, 788, imports=['kernel32.dll']))
        struct.pack_into('<I', data, 640 + 12, 0xfffffff0)
        path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'unmapped PE export RVA'):
            package.pe_info(path)

    def test_pe_directory_extent_and_delayed_import_contract(self):
        import struct
        path = self.root / 'header.dll'
        for machine in [0x14c, 0x8664]:
            directory = 152 + (96 if machine == 0x14c else 112)
            for count in [0, 1, 17, 0xffffffff]:
                data = bytearray(pe(machine, 690, imports=['kernel32.dll']))
                struct.pack_into('<I', data, directory - 4, count)
                path.write_bytes(data)
                with self.assertRaisesRegex(ValueError, 'invalid PE directory extent'):
                    package.pe_info(path)
            data = bytearray(pe(machine, 690))
            # Declared count cannot borrow directory entries from section bytes.
            struct.pack_into('<H', data, 128 + 20, directory - 152 + 8)
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'invalid PE directory extent'):
                package.pe_info(path)
            for imported in ['kernel32.dll', 'api-ms-win-core-synch-l1-2-0.dll']:
                path.write_bytes(pe(machine, 690, delay_imports=[imported]))
                with self.assertRaisesRegex(ValueError, 'delayed import directory'):
                    package.pe_info(path)

    def test_source_archive_stubs_verify_checksum_members_and_exact_hashes(self):
        archive = self.root / 'ge.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for arch, machine in [('x86_64-windows', 0x8664), ('i386-windows', 0x14c)]:
                data = pe(machine)
                member = tarfile.TarInfo('GE-Proton11-7-x86_64/files/lib/wine/' + arch + '/nvcuda.dll')
                member.size = len(data)
                stream.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, 'archive checksum mismatch'):
            package.archive_stubs(archive, self.tool)
        with mock.patch.object(policy, 'GE_ARCHIVE_SHA256', package.digest(archive)):
            package.archive_stubs(archive, self.tool)
            for arch in policy.STUB_SHA256:
                path = self.tool / 'files/lib/wine-cuda/stub' / arch / 'nvcuda.dll'
                self.assertEqual(package.digest(path), policy.STUB_SHA256[arch])
        with tarfile.open(archive, 'w:gz') as stream:
            pass
        with mock.patch.object(policy, 'GE_ARCHIVE_SHA256', package.digest(archive)):
            with self.assertRaisesRegex(ValueError, 'lacks both'):
                package.archive_stubs(archive, self.tool)

    def test_generated_sdk_needs_no_old_crossfile_winegcc_or_import_libraries(self):
        build, sdk = self.root / 'build', self.root / 'sdk'
        source, obj = build / 'src/wine', build / 'obj-x86_64'
        for path in [source / 'include/windef.h', source / 'include/wine/server_protocol.h', obj / 'include/wtypes.h']:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        tool = obj / 'tools/winebuild/winebuild'
        tool.parent.mkdir(parents=True)
        tool.write_text('#!/bin/sh\nexit 0\n')
        tool.chmod(0o755)
        record = wine_sdk.prepare(build, sdk, generated_bridge=True)
        self.assertEqual(record['profile'], 'generated')
        self.assertEqual(list(record['wrappers']), ['winebuild'])
        self.assertFalse((sdk / 'bin/winegcc').exists())
        self.assertFalse((sdk / 'nvcuda-wine64.ini').exists())
        tool.unlink()
        with self.assertRaisesRegex(ValueError, 'prepared Wine SDK lacks'):
            wine_sdk.prepare(build, sdk, generated_bridge=True)
