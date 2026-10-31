"""Extracted archive admission, including compiler-observed nested inputs."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import source_archive


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), '-c', 'user.name=Christian Kündig',
        '-c', 'user.email=christian@kuendig.info', '-c', 'commit.gpgsign=false',
        '-c', 'core.hooksPath=/dev/null', *args], stderr=subprocess.PIPE).decode().strip()


class SourceArchive(unittest.TestCase):
    def sdk_fixture(self, root, source_id):
        sdk = root / 'prepared-sdk'
        for name, data in [('bin/winebuild', b'portable tool'), ('lib/ntdll.so', b'prepared provider')]:
            path = sdk / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        receipt = sdk / 'ABI-RECEIPT.json'
        receipt.write_text(json.dumps({'sdk_files': {name: source_archive.sha256(sdk / name)
            for name in ['bin/winebuild', 'lib/ntdll.so']}}))
        return {'WINE_SDK_ROOT':str(sdk), 'ZLUDA_WINE_ABI_RECEIPT':str(receipt),
                'ZLUDA_WINE_ABI_RECEIPT_SHA256':source_archive.sha256(receipt),
                'ZLUDA_WINE_SOURCE_ID':source_id, 'SOURCE_DATE_EPOCH':'1234567'}

    def test_full_tracked_source_and_only_observed_submodule_files_extract_and_resolve(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, build = root / 'source', root / 'build'
            source.mkdir(); build.mkdir()
            git(source, 'init', '-q')
            files = {'wine/zluda_wine/Cargo.toml': '[package]\nname="fixture"\nversion="0.1.0"\n',
                     'wine/zluda_wine/src/lib.rs': 'include_str!("../../../ext/fixture-sys/src/lib.rs");',
                     'wine/zluda_wine/build.rs': 'fn main() {}',
                     'ext/fixture-sys/src/lib.rs': '// macro input\n',
                     'zluda/src/lib.rs': '// attestation\n', 'zluda/src/impl/mod.rs': '// refusal\n',
                     'not-guessed-by-a-hand-list.txt': 'retained',
                     'wine/zluda_wine/tests/lgpl/reference.rs': 'include_str!("missing-test-only.h");',
                     'wine/zluda_wine/LICENSES/PROVENANCE.md': 'production/reference boundary'}
            for name, content in files.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            child = source / 'ext/nested'
            child.mkdir(); git(child, 'init', '-q')
            (child / 'read.h').write_text('observed nested input')
            (child / 'large-unread.h').write_text('not read')
            git(child, 'add', '.'); git(child, 'commit', '-qm', 'nested')
            git(source, 'add', '.'); git(source, 'commit', '-qm', 'source')
            (build / 'fixture.d').write_text('out: ' + str(source / 'wine/zluda_wine/src/lib.rs') + ' ' + str(source / 'cuda_macros/../ext/fixture-sys/src/lib.rs') + ' ' + str(child / 'read.h') + "\n# env-dep:DESCRIPTION=Wine's escaped comment\n")
            (build / 'output').write_text('cargo:rerun-if-changed=' + str(source / 'zluda/src/impl/mod.rs') + '\n')
            cargo = root / 'cargo'
            cargo.write_text('#!/usr/bin/env python3\nimport json,sys,pathlib\n'
                'assert sys.argv[1] in ["metadata", "check"] and "--locked" in sys.argv and "--offline" in sys.argv\n'
                'if sys.argv[1]=="check":\n'
                ' import os\n'
                ' assert "WINE_SOURCE_ROOT" not in os.environ\n'
                ' sdk=pathlib.Path(os.environ["WINE_SDK_ROOT"])\n'
                ' assert (sdk/"lib/ntdll.so").read_bytes()==b"prepared provider"\n'
                ' assert "archive-check-" in str(sdk)\n'
                ' assert "archive-check-" in os.environ["ZLUDA_WINE_ABI_RECEIPT"]\n'
                'print(json.dumps({"packages":[{"source":None,"manifest_path":sys.argv[-1]}]}))\n')
            cargo.chmod(0o755)
            output = root / 'archive.tar.gz'
            env = self.sdk_fixture(root, git(source, 'rev-parse', 'HEAD'))
            # The original source path may be unavailable; production is source-free.
            with mock.patch.dict(os.environ, dict(env, WINE_SOURCE_ROOT='/missing/original-wine')):
                record = source_archive.create(source, build, output, str(cargo))
            with tarfile.open(output) as archive:
                names = set(archive.getnames())
                self.assertTrue(set(files) <= names)
                self.assertIn('ext/nested/read.h', names)
                self.assertNotIn('ext/nested/large-unread.h', names)
                self.assertIn('external/prepared-wine-sdk/lib/ntdll.so', names)
                self.assertIn('external/prepared-wine-abi.json', names)
                self.assertFalse(any(n.startswith('external/wine-source/') for n in names))
            self.assertEqual(record['prepared_abi_receipt_sha256'], env['ZLUDA_WINE_ABI_RECEIPT_SHA256'])
            self.assertEqual(record['resolved_includes'], ['ext/fixture-sys/src/lib.rs'])
            self.assertEqual(set(record['extra_submodule_files']), {'ext/nested/read.h'})
            (source / 'wine/zluda_wine/src/lib.rs').write_text('include_str!("missing.rs");')
            git(source, 'add', '.'); git(source, 'commit', '-qm', 'missing include')
            with mock.patch.dict(os.environ, self.sdk_fixture(root, git(source, 'rev-parse', 'HEAD'))):
                with self.assertRaisesRegex(ValueError, 'unresolved extracted include'):
                    source_archive.create(source, build, output, str(cargo))

    def test_missing_dep_info_or_untracked_compiler_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, build = root / 'source', root / 'build'
            source.mkdir(); build.mkdir(); git(source, 'init', '-q')
            (source / 'tracked').write_text('source')
            git(source, 'add', '.'); git(source, 'commit', '-qm', 'source')
            with self.assertRaisesRegex(ValueError, 'no Cargo dep-info'):
                source_archive.observed_inputs(source, build)
            (source / 'untracked').write_text('not an admitted input')
            (build / 'fixture.d').write_text('out: ' + str(source / 'untracked'))
            with self.assertRaisesRegex(ValueError, 'untracked build source'):
                source_archive.create(source, build, root / 'source.tar.gz')

    def test_offline_rebuild_uses_extracted_source_for_all_three_targets(self):
        import io
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'source.tar.gz'
            env = self.sdk_fixture(root, 'a' * 40)
            capsule = source_archive.sdk_inputs(env)
            capsule['external/prepared-wine-abi.json'] = Path(env['ZLUDA_WINE_ABI_RECEIPT']).read_bytes()
            capsule['external/BRIDGE-BUILD.json'] = json.dumps({'source_commit':'a'*40,
                'source_date_epoch':'1234567', 'abi_receipt_sha256':env['ZLUDA_WINE_ABI_RECEIPT_SHA256']}).encode()
            with tarfile.open(archive, 'w:gz') as output:
                for name, value in dict(capsule, **{'wine/zluda_wine/Cargo.toml': b'fixture'}).items():
                    info = tarfile.TarInfo(name)
                    info.size = len(value)
                    output.addfile(info, io.BytesIO(value))
            calls = []
            def build(command, **kw):
                self.assertEqual(command[1], 'build')
                self.assertTrue({'--locked', '--offline', '--release', '--lib'} <= set(command))
                env = kw['env']
                self.assertEqual(env['CARGO_BUILD_JOBS'], '3')
                self.assertNotIn('WINE_SOURCE_ROOT', env)
                self.assertNotIn('ZLUDA_WINE_WINE_INCLUDE', env)
                self.assertEqual((Path(env['WINE_SDK_ROOT']) / 'lib/ntdll.so').read_bytes(), b'prepared provider')
                self.assertEqual(env['ZLUDA_WINE_SOURCE_ID'], 'a'*40)
                self.assertEqual(kw['cwd'], root / 'rebuild/source/wine/zluda_wine')
                target = command[command.index('--target') + 1]
                if 'windows' in target:
                    self.assertIn('ZLUDA_WINE_PE_CC', env)
                    self.assertIn('ZLUDA_WINE_PE_AR', env)
                artifact = Path(env['CARGO_TARGET_DIR']) / target / 'release' / (
                    'libnvcuda.so' if 'linux' in target else 'libnvcuda.a')
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(target.encode())
                calls.append(target)
            with mock.patch.dict(os.environ, {'WINE_SDK_ROOT': '/prepared-sdk'}), \
                 mock.patch.object(source_archive.subprocess, 'run', side_effect=build):
                record = source_archive.rebuild(archive, root / 'rebuild')
            self.assertEqual(record['status'], 'passed')
            self.assertEqual(set(calls), {'x86_64-unknown-linux-gnu', 'x86_64-pc-windows-gnu', 'i686-pc-windows-gnu'})
            self.assertEqual(json.loads((root / 'rebuild/REBUILD.json').read_text()), record)
