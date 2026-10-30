"""Bounded artifact staging must never walk Wine's z: or prefix contents."""
import hashlib
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging/zluda-overlay'))
import stage_cpu_evidence as gate


class CpuEvidence(unittest.TestCase):
    def write(self, root, name, value='gate receipt'):
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def test_logs_results_binaries_and_migration_are_staged_without_visiting_z_drive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dest = root/'wine-cpu-evidence'
            expected = ['wine-cpu.log', 'wine-cpu/run/INTEGRATION.json',
                        'wine-cpu/run/build/probe.exe', 'wine-cpu/run/production/x86_64-unix/nvcuda.so',
                        'wine-cpu/run/lifecycle/results/free-library-pe.log']
            outside = root/'host-boot-efi'
            self.write(outside, 'host-secret.log', 'MUST NOT BE READ')
            for arch in gate.PROFILES:
                base = f'wine-cpu/run/launcher/{arch}'
                expected += [f'wine-cpu/run/{arch}/results/RESULT.json',
                             f'{base}/compatdata/zluda-cuda-roots-v1.json']
                for phase in gate.PHASES:
                    expected += [f'{base}/{phase}/launcher.log', f'{base}/{phase}/backend.json']
                prefix = root/base/'compatdata/pfx'
                self.write(prefix, 'drive_c/windows/user-private.json', 'PREFIX MUST NOT BE READ')
                (prefix/'dosdevices').mkdir()
                (prefix/'dosdevices/z:').symlink_to(outside, target_is_directory=True)
                (prefix/'dosdevices/c:').symlink_to('../drive_c', target_is_directory=True)
            for name in expected:
                self.write(root, name)
            listdir = os.listdir
            visited = []
            def bounded_listdir(fd):
                path = Path(os.readlink('/proc/self/fd/'+str(fd)))
                visited.append(path)
                self.assertFalse(path.is_relative_to(outside))
                self.assertTrue(not (set(path.relative_to(root).parts) & gate.EXCLUDED))
                return listdir(fd)
            with mock.patch.object(gate.os, 'listdir', side_effect=bounded_listdir):
                record = gate.stage(root, dest)
            self.assertTrue(visited)
            self.assertEqual(set(record['files']), set(expected))
            self.assertEqual(record['errors'], [])
            for name in expected:
                path = dest/name
                self.assertFalse(path.is_symlink())
                self.assertEqual(path.read_bytes(), (root/name).read_bytes())
                self.assertEqual(record['files'][name]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(json.loads((dest/'STAGING.json').read_text()), record)
            self.assertFalse(list(dest.rglob('dosdevices')))
            self.assertFalse(list(dest.rglob('drive_c')))
            self.assertFalse(list(dest.rglob('host-secret.log')))

    def test_directory_and_file_symlinks_and_special_files_are_not_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root/'outside'
            self.write(outside, 'leaked.json', 'MUST NOT BE READ')
            build = root/'wine-cpu/run/build'
            build.mkdir(parents=True)
            (build/'unsafe-dir').symlink_to(outside, target_is_directory=True)
            (build/'unsafe.json').symlink_to(outside/'leaked.json')
            os.mkfifo(build/'pipe.log')
            (root/'wine-cpu/run/production').symlink_to(outside, target_is_directory=True)
            (root/'wine-cpu.log').symlink_to(outside/'leaked.json')
            case = root/'wine-cpu/run/launcher/win64/on'
            case.parent.mkdir(parents=True)
            case.symlink_to(outside, target_is_directory=True)
            dest = root/'wine-cpu-evidence'
            record = gate.stage(root, dest)
            self.assertEqual(record['files'], {})
            self.assertTrue(set(['wine-cpu.log','wine-cpu/run/build/unsafe-dir',
                'wine-cpu/run/build/unsafe.json','wine-cpu/run/build/pipe.log',
                'wine-cpu/run/production','wine-cpu/run/launcher/win64/on']) <= set(record['skipped']))
            self.assertEqual([path.name for path in dest.iterdir()], ['STAGING.json'])

    def test_partial_failed_run_still_gets_a_receipt_and_existing_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write(root, 'wine-cpu.log', 'runtime failed before results')
            dest = root/'wine-cpu-evidence'
            record = gate.stage(root, dest)
            self.assertEqual(set(record['files']), {'wine-cpu.log'})
            self.assertTrue(record['missing'])
            with self.assertRaises(FileExistsError):
                gate.stage(root, dest)
            with self.assertRaises(ValueError):
                gate.stage(root, root/'wine-cpu')

    def test_workflow_uploads_only_the_staged_directory_without_recursive_launcher_globs(self):
        workflow = (ROOT/'.github/workflows/zluda-overlay.yml').read_text()
        stage = workflow.split('      - name: Stage bounded CPU evidence without prefix traversal\n',1)[1]
        self.assertIn('        if: always()\n', stage)
        self.assertIn('stage_cpu_evidence.py', stage)
        upload = stage.split('      - name: Retain CPU fixture evidence on success or failure\n',1)[1]
        upload = upload.split('      - uses:',1)[0]
        self.assertIn('        if: always()\n', upload)
        self.assertIn('          path: ${{ runner.temp }}/wine-cpu-evidence\n', upload)
        self.assertNotIn('**', upload)
        self.assertNotIn('/launcher', upload)
        self.assertNotIn('/wine-cpu/run', upload)

    def test_an_unreadable_evidence_tree_retains_other_files_and_an_error_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write(root, 'wine-cpu.log', 'runtime output')
            build = root/'wine-cpu/run/build'
            build.mkdir(parents=True)
            listdir = os.listdir
            def denied(fd):
                if Path(os.readlink('/proc/self/fd/'+str(fd))) == build:
                    raise PermissionError(errno.EACCES, 'fixture permission denied')
                return listdir(fd)
            dest = root/'wine-cpu-evidence'
            with mock.patch.object(gate.os, 'listdir', side_effect=denied):
                record = gate.stage(root, dest)
            self.assertEqual(record['status'], 'incomplete')
            self.assertEqual(record['errors'][0]['path'], 'wine-cpu/run/build')
            self.assertEqual(set(record['files']), {'wine-cpu.log'})
            self.assertEqual(json.loads((dest/'STAGING.json').read_text()), record)
