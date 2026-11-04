import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/zluda-overlay'))
import product_identity as gate


def archive(files, mode=0o644):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as stream:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            stream.addfile(info, io.BytesIO(data))
    return output.getvalue()


class ProductIdentity(unittest.TestCase):
    def test_archive_parser_rejects_duplicate_and_parent_paths(self):
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            gate.inventory(io.BytesIO(archive({'../escape': b'x'})), False)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as out:
            for _ in range(2):
                out.addfile(tarfile.TarInfo('duplicate'))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            gate.inventory(io.BytesIO(stream.getvalue()), False)

    def test_changed_production_source_is_unclassified(self):
        before = archive({'wine/zluda_wine/src/bridge.rs': b'production'})
        after = archive({'wine/zluda_wine/src/bridge.rs': b'changed'})
        result = gate.nested_changes('sources/zluda-wine.tar.gz', before, after)
        self.assertEqual(result['unclassified'], ['wine/zluda_wine/src/bridge.rs'])

    def test_arbitrary_provenance_field_cannot_hide_runtime_change(self):
        name = 'provenance/PROVENANCE.json'
        self.assertIsNone(gate.metadata_change(name, b'{"arbitrary":1}', b'{"arbitrary":2}'))
        actual = {'proton': gate.PROFILE['source_commit'], 'component_build_proton': gate.PROFILE['source_commit'],
                  'input_lock_sha256': gate.sha(gate.LOCK_FILE.read_bytes()), 'components': gate.LOCK['components'],
                  'wine_bridge': gate.LOCK['wine_bridge'], 'packaging': gate.packaging_identity()}
        result = gate.metadata_change(name, json.dumps(dict(actual, proton='old')).encode(), json.dumps(actual).encode())
        self.assertEqual(result['changed_fields'], ['proton'])
        forged = dict(actual, packaging=dict(actual['packaging'], commit='0'*40))
        self.assertIsNone(gate.metadata_change(name, json.dumps(actual).encode(), json.dumps(forged).encode()))

    def test_bridge_contract_allows_only_its_pinned_source_identity(self):
        name = 'provenance/ZLUDA-WINE.json'
        before = {'contract': {'source_commit': 'a'*40, 'modules': ['fixed']}, 'sha256': {'module': 'fixed'}}
        after = {'contract': dict(before['contract'], source_commit=gate.LOCK['components'][gate.bridge_component()]),
                 'sha256': before['sha256']}
        self.assertIsNotNone(gate.metadata_change(name, json.dumps(before).encode(), json.dumps(after).encode()))
        for changed in [dict(after, sha256={}), dict(after, contract=dict(after['contract'], modules=[]))]:
            self.assertIsNone(gate.metadata_change(name, json.dumps(before).encode(), json.dumps(changed).encode()))

    def test_removed_tool_must_be_in_reviewed_scope(self):
        source = 'wine/w1-fixtures/tools/cuda-count/count.c'
        result = gate.nested_changes('capsule', archive({source: b'old counter'}), archive({}))
        self.assertEqual(result['unclassified'], [])
        result = gate.nested_changes('capsule', archive({'unknown.c': b'input'}), archive({}))
        self.assertEqual(result['unclassified'], ['unknown.c'])

    def test_prepared_commit_metadata_cannot_change_sdk_or_source_hashes(self):
        before = {'prepared_commit': 'a'*40, 'sdk_files': {'lib/ntdll.so': 'fixed'},
                  'prepared_files': {'include/ntuser.h': 'fixed'}}
        after = dict(before, prepared_commit='b'*40)
        self.assertIsNotNone(gate.prepared_identity(json.dumps(before).encode(), json.dumps(after).encode(), 'b'*40))
        for field in ['sdk_files', 'prepared_files']:
            changed = dict(after, **{field: {}})
            self.assertIsNone(gate.prepared_identity(json.dumps(before).encode(), json.dumps(changed).encode(), 'b'*40))

    def test_recipe_directory_cannot_be_replaced_by_a_link(self):
        with mock.patch.object(gate, 'git', return_value='tree'):
            self.assertIsNotNone(gate.recipe_change('profiles', None, {'type': '5'}))
            self.assertIsNone(gate.recipe_change('profiles', None, {'type': '2', 'target': '/outside'}))

    def test_source_receipt_binds_every_external_member_and_actual_archive(self):
        abi = json.dumps({'prepared_commit': 'a'*40}).encode()
        capsule = archive({'external/prepared-wine-abi.json': abi, 'external/SDK': b'provider'})
        record = {'source_commit': gate.LOCK['components'][gate.bridge_component()], 'source_tree': 'c'*40,
                  'archive_sha256': gate.sha(capsule), 'compiled_source_closure': ['src/bridge.rs'],
                  'prepared_abi_receipt_sha256': gate.sha(abi),
                  'sdk_capsule': {'external/prepared-wine-abi.json': gate.sha(abi),
                                  'external/SDK': gate.sha(b'provider')}}
        before = json.dumps(dict(record, archive_sha256='old')).encode()
        contents = {'sources/zluda-wine.tar.gz': capsule}
        name = 'sources/zluda-wine.tar.gz.json'
        with mock.patch.object(gate, 'git', return_value='c'*40):
            self.assertIsNotNone(gate.support_change(name, before, json.dumps(record).encode(), contents))
        for field, value in [('archive_sha256', 'forged'), ('sdk_capsule', {}),
                             ('compiled_source_closure', ['different.rs'])]:
            changed = dict(record, **{field: value})
            with mock.patch.object(gate, 'git', return_value='c'*40):
                self.assertIsNone(gate.support_change(name, before, json.dumps(changed).encode(), contents))

    def test_build_log_allows_only_temporary_paths_and_elapsed_time(self):
        name = 'sources/zluda-wine.tar.gz.check.log'
        before = b'Compiling /work/archive-check-aaaa/wine\n    Finished dev in 8.64s\n'
        after = b'Compiling /work/archive-check-bbbb/wine\n    Finished dev in 6.46s\n'
        self.assertIsNotNone(gate.support_change(name, before, after, {}))
        self.assertIsNone(gate.support_change(name, before, after+b'error: broken\n', {}))

    def test_hardlinks_are_hashed_and_cycles_or_link_targets_are_rejected(self):
        def linked(target, kind=tarfile.REGTYPE):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w') as out:
                info = tarfile.TarInfo('target')
                info.type = kind
                if kind == tarfile.REGTYPE:
                    info.size = 7
                    out.addfile(info, io.BytesIO(b'runtime'))
                else:
                    info.linkname = 'alias'
                    out.addfile(info)
                link = tarfile.TarInfo('alias')
                link.type, link.linkname = tarfile.LNKTYPE, target
                out.addfile(link)
            return io.BytesIO(stream.getvalue())
        entries, _ = gate.inventory(linked('target'), False)
        self.assertEqual(entries['alias']['sha256'], gate.sha(b'runtime'))
        for stream in [linked('../target'), linked('missing'), linked('target', tarfile.LNKTYPE),
                       linked('target', tarfile.SYMTYPE)]:
            with self.assertRaises(ValueError):
                gate.inventory(stream, False)

    def test_selected_lock_and_sbom_reject_deleted_or_forged_provenance(self):
        encode = lambda x: json.dumps(x).encode()
        before = dict(gate.LOCK, proton_source='old')
        name = 'provenance/inputs.lock.json'
        self.assertIsNotNone(gate.metadata_change(name, encode(before), encode(gate.LOCK)))
        self.assertIsNone(gate.metadata_change(name, encode(before), encode(dict(gate.LOCK, components={}))))
        name = 'provenance/bridges-SBOM.json'
        old = {'sources': {'old': 'pin'}, 'license_files': ['licenses/fixed']}
        actual = {'sources': {n:gate.LOCK['components'][n] for n in gate.components_for_part('bridges')},
                  'license_files': old['license_files']}
        self.assertIsNotNone(gate.metadata_change(name, encode(old), encode(actual)))
        for forged in [dict(actual, sources={}), dict(actual, license_files=[])]:
            self.assertIsNone(gate.metadata_change(name, encode(old), encode(forged)))
        abi = {'prepared_commit': 'a'*40, 'sdk_files': {'provider': 'fixed'}}
        self.assertIsNone(gate.prepared_identity(encode(abi), encode(dict(abi, prepared_commit='0'*40)), 'b'*40))

    def test_inventory_row_removal_preserves_all_other_provenance(self):
        encode = lambda x: json.dumps(x).encode()
        old = {'contract': 'fixed', 'source_inputs': {'wine/zluda_wine/build-pilot.sh': 'retired', 'src/bridge.rs': 'fixed'}}
        new = {'contract': 'fixed', 'source_inputs': {'src/bridge.rs': 'fixed'}}
        self.assertIsNotNone(gate.production_inventory(encode(old), encode(new)))
        for forged in [dict(new, contract='changed'), dict(new, source_inputs={}),
                       dict(new, source_inputs={'src/bridge.rs': 'forged'})]:
            self.assertIsNone(gate.production_inventory(encode(old), encode(forged)))

    def test_product_comparison_rejects_runtime_mutation_deletion_and_mode(self):
        import tempfile
        def product(runtime=b'runtime', mode=0o644):
            files = {'tool/SYMLINKS.json': b'{}'}
            if runtime is not None:
                files['tool/files/runtime.dll'] = runtime
            manifest = ''.join(gate.sha(data)+'  '+name.removeprefix('tool/')+'\n' for name,data in sorted(files.items())).encode()
            files['tool/MANIFEST'] = manifest
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w:gz') as stream:
                for name,data in files.items():
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), mode if name.endswith('runtime.dll') else 0o644
                    stream.addfile(info, io.BytesIO(data))
            return output.getvalue(), manifest
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            control, cm = product()
            (root/'control').write_bytes(control)
            (root/'cm').write_bytes(cm)
            for data, mode in [(b'changed', 0o644), (None, 0o644), (b'runtime', 0o755)]:
                candidate, nm = product(data, mode)
                (root/'candidate').write_bytes(candidate)
                (root/'nm').write_bytes(nm)
                with mock.patch.object(gate, 'CONTROL_TAR', gate.sha(control)), self.assertRaisesRegex(ValueError, 'unclassified'):
                    gate.compare(root/'control', root/'candidate', root/'cm', root/'nm', root/'proof')
                self.assertIn('files/runtime.dll', json.loads((root/'proof').read_text())['unclassified'])

    def test_baseline_receipt_binds_tree_parent_hash_and_selected_inputs(self):
        facts = gate.PROFILE['wine_prepared_baseline']
        raw = ('tree '+facts['tree']+'\nparent '+gate.LOCK['components']['wine']+
               '\nauthor Christian Kündig <christian@kuendig.info> 1793802357 +0000'+
               '\ncommitter Christian Kündig <christian@kuendig.info> 1793802357 +0000'+
               '\n\nprep/base-'+gate.LOCK['ge_source'][:12]+'\n\nAssisted-by: Codex:gpt-6.1-sol\n').encode()
        digest = gate.hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        contents = {'provenance/WINE-BASELINE-COMMIT': raw,
                    'provenance/WINE-SCOPE.json': json.dumps({'baseline':digest}).encode(),
                    'provenance/WINE-OVERLAY': ('header\nbase_inputs='+facts['base_inputs']+'\n').encode()}
        self.assertEqual(gate.baseline_commit(contents), digest)
        for bad in [raw.replace(facts['tree'].encode(), b'0'*40),
                    raw.replace(gate.LOCK['components']['wine'].encode(), b'0'*40),
                    raw.replace(b'\nauthor ', b'\nparent '+b'0'*40+b'\nauthor '),
                    raw+b'forged\n']:
            altered = dict(contents, **{'provenance/WINE-BASELINE-COMMIT': bad})
            self.assertIsNone(gate.baseline_commit(altered))
        altered = dict(contents, **{'provenance/WINE-OVERLAY': b'base_inputs=forged\n'})
        self.assertIsNone(gate.baseline_commit(altered))

    def test_admitted_fixture_content_cannot_hide_executable_mode_change(self):
        name = 'wine/w1-fixtures/src/identity.c'
        before = archive({name:b'original'})
        with mock.patch.object(gate.subprocess, 'check_output', return_value=b'compacted'):
            accepted = gate.nested_changes('capsule', before, archive({name:b'compacted'}))
            self.assertEqual(accepted['unclassified'], [])
            rejected = gate.nested_changes('capsule', before, archive({name:b'compacted'}, mode=0o755))
            self.assertEqual(rejected['unclassified'], [name])

    def test_build_output_count_change_and_backend_digest_are_exactly_bound(self):
        inventory = {'source_inputs': {'wine/zluda_wine/build-pilot.sh':'retired', **{'input'+str(i):'fixed' for i in range(59)}}}
        retired = {'source_inputs': {k:v for k,v in inventory['source_inputs'].items() if not k.endswith('build-pilot.sh')}}
        old_inventory, new_inventory = json.dumps(inventory).encode(), json.dumps(retired).encode()
        line = lambda count: json.dumps({'production_input_closure':'passed', 'files':count,
                                        'adapter_headers':['stdint.h','stddef.h']})+'\n'
        old_text = 'cargo:rerun-if-changed=/src/fixed\n'+line(60)+'cargo:rustc-cfg=fixed\n'
        new_text = old_text.replace(line(60), line(59))
        old = [{'dep_info':'release/build/bridge/output', 'sha256':gate.sha(old_text.encode()), 'source_inputs':['fixed']}]
        new = [dict(old[0], sha256=gate.sha(new_text.encode()), build_output=new_text)]
        self.assertTrue(gate.cargo_observations(old,new,old_inventory,new_inventory))
        for bad in [new_text+line(59), new_text.replace(line(59),line(58)),
                    new_text.replace('cargo:rustc-cfg=fixed','cargo:rustc-cfg=forged')]:
            forged = [dict(new[0], sha256=gate.sha(bad.encode()), build_output=bad)]
            self.assertFalse(gate.cargo_observations(old,forged,old_inventory,new_inventory))
        self.assertFalse(gate.cargo_observations(old,new,old_inventory,json.dumps({'source_inputs':{}}).encode()))
        backend_old = [dict(old[0], source_inputs=['backend/fixed'])]
        backend_new = [dict(backend_old[0], sha256=new[0]['sha256'])]
        self.assertTrue(gate.backend_observations(backend_old,backend_new,old,new,old_inventory,new_inventory))
        for forged in [dict(backend_new[0],sha256='0'*64), dict(backend_new[0],source_inputs=[])]:
            self.assertFalse(gate.backend_observations(backend_old,[forged],old,new,old_inventory,new_inventory))

    def test_build_log_permits_only_leading_compile_banner_permutation(self):
        name = 'sources/zluda-wine.tar.gz.check.log'
        before = b'   Compiling quote v1.0.45\n   Compiling unicode-ident v1.0.13\nwarning: fixed\n'
        after = b'   Compiling unicode-ident v1.0.13\n   Compiling quote v1.0.45\nwarning: fixed\n'
        self.assertIsNotNone(gate.support_change(name,before,after,{}))
        for bad in [after.replace(b'warning: fixed',b'warning: changed'),
                    b'   Compiling extra v1.0.0\n'+after, after.replace(b'v1.0.45',b'v1.0.46'),
                    after+after.splitlines(keepends=True)[0], after.replace(b'   Compiling quote v1.0.45\n',b''),
                    after+b'warning: other\n   Compiling quote v1.0.45\n']:
            self.assertIsNone(gate.support_change(name,before,bad,{}))

        tail_a = before+b'   Compiling first v1.0.0\n   Compiling second v1.0.0\n'
        tail_b = before+b'   Compiling second v1.0.0\n   Compiling first v1.0.0\n'
        self.assertIsNone(gate.support_change(name,tail_a,tail_b,{}))

    def test_packaging_identity_rejects_nonpublic_or_credentialed_origins(self):
        import inputs
        with mock.patch.object(inputs, 'git', side_effect=['c'*40, 'https://github.com/owner/packaging.git']):
            self.assertEqual(inputs.packaging_identity(), {'commit':'c'*40, 'repository':'https://github.com/owner/packaging'})
        for remote in ['https://token@github.com/owner/packaging.git', 'https://github.com/owner/packaging?token=x',
                       'https://github.com/owner/packaging#secret', '/local/source', 'git@github.com:owner/packaging.git',
                       'https://elsewhere.example/owner/packaging.git']:
            with mock.patch.object(inputs, 'git', side_effect=['c'*40,remote]), self.assertRaises(ValueError):
                inputs.packaging_identity()
