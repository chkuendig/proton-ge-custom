"""Extracted archive admission, including compiler-observed nested inputs."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import source_archive


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), '-c', 'user.name=Christian Kündig',
        '-c', 'user.email=christian@kuendig.info', '-c', 'commit.gpgsign=false',
        '-c', 'core.hooksPath=/dev/null', *args], stderr=subprocess.PIPE).decode().strip()


class SourceArchive(unittest.TestCase):
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
                     'not-guessed-by-a-hand-list.txt': 'retained'}
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
                'print(json.dumps({"packages":[{"source":None,"manifest_path":sys.argv[-1]}]}))\n')
            cargo.chmod(0o755)
            output = root / 'archive.tar.gz'
            record = source_archive.create(source, build, output, str(cargo))
            with tarfile.open(output) as archive:
                names = set(archive.getnames())
                self.assertTrue(set(files) <= names)
                self.assertIn('ext/nested/read.h', names)
                self.assertNotIn('ext/nested/large-unread.h', names)
            self.assertEqual(record['resolved_includes'], ['ext/fixture-sys/src/lib.rs'])
            self.assertEqual(set(record['extra_submodule_files']), {'ext/nested/read.h'})
            (source / 'wine/zluda_wine/src/lib.rs').write_text('include_str!("missing.rs");')
            git(source, 'add', '.'); git(source, 'commit', '-qm', 'missing include')
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
