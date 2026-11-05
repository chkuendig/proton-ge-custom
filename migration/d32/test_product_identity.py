import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packaging/zluda-overlay'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import product_identity as gate


def archive(files, mode=0o644):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as stream:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            stream.addfile(info, io.BytesIO(data))
    return output.getvalue()


def production_inventories():
    before = (Path(__file__).parent / 'PRODUCTION-INPUTS.before.json').read_bytes()
    document = json.loads(before)
    del document['source_inputs']['wine/zluda_wine/build-pilot.sh']
    return before, (json.dumps(document, indent=2) + '\n').encode()


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
                  'wine_bridge': gate.LOCK['wine_bridge'], 'component_patches': gate.LOCK['component_patches'],
                  'packaging': gate.packaging_identity()}
        result = gate.metadata_change(name, json.dumps(dict(actual, proton='old')).encode(), json.dumps(actual).encode())
        self.assertEqual(result['changed_fields'], ['proton'])
        forged = dict(actual, packaging=dict(actual['packaging'], commit='0'*40))
        self.assertIsNone(gate.metadata_change(name, json.dumps(actual).encode(), json.dumps(forged).encode()))
        self.assertIsNone(gate.metadata_change(name, json.dumps(actual).encode(), json.dumps(dict(actual, component_patches={})).encode()))

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
        result = gate.nested_changes('sources/zluda-wine.tar.gz', archive({source: b'old counter'}), archive({}))
        self.assertEqual(result['unclassified'], [])
        result = gate.nested_changes('sources/zluda-wine.tar.gz', archive({'unknown.c': b'input'}), archive({}))
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
            self.assertIsNotNone(gate.recipe_change('profiles', None, {'type': '5', 'mode': 0o755}))
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
        before, after = production_inventories()
        self.assertIsNotNone(gate.production_inventory(before, after))
        document = json.loads(after)
        for forged in [dict(document, contract='changed'), dict(document, source_inputs={}),
                       dict(document, source_inputs=dict(document['source_inputs'], extra='forged'))]:
            self.assertIsNone(gate.production_inventory(before, json.dumps(forged).encode()))

    def test_product_comparison_rejects_runtime_mutation_deletion_and_mode(self):
        import tempfile
        def product(runtime=b'runtime', mode=0o644, root_mode=0o755):
            files = {'tool/SYMLINKS.json': b'{}'}
            if runtime is not None:
                files['tool/files/runtime.dll'] = runtime
            manifest = ''.join(gate.sha(data)+'  '+name.removeprefix('tool/')+'\n' for name,data in sorted(files.items())).encode()
            files['tool/MANIFEST'] = manifest
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w:gz') as stream:
                directory = tarfile.TarInfo('tool')
                directory.type, directory.mode = tarfile.DIRTYPE, root_mode
                stream.addfile(directory)
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
            for data, mode, root_mode, rejected in [(b'changed', 0o644, 0o755, 'files/runtime.dll'),
                    (None, 0o644, 0o755, 'files/runtime.dll'), (b'runtime', 0o755, 0o755, 'files/runtime.dll'),
                    (b'runtime', 0o644, 0o700, '.')]:
                candidate, nm = product(data, mode, root_mode)
                (root/'candidate').write_bytes(candidate)
                (root/'nm').write_bytes(nm)
                with mock.patch.object(gate, 'CONTROL_TAR', gate.sha(control)), mock.patch.dict(gate.LOCK, tool_name='tool'), self.assertRaisesRegex(ValueError, 'unclassified'):
                    gate.compare(root/'control', root/'candidate', root/'cm', root/'nm', root/'proof')
                self.assertIn(rejected, json.loads((root/'proof').read_text())['unclassified'])

    def test_baseline_receipt_binds_tree_parent_hash_and_selected_inputs(self):
        facts = json.loads((Path(gate.__file__).parent / 'BASELINE.json').read_text())
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
            accepted = gate.nested_changes('sources/zluda-wine.tar.gz', before, archive({name:b'compacted'}))
            self.assertEqual(accepted['unclassified'], [])
            rejected = gate.nested_changes('sources/zluda-wine.tar.gz', before, archive({name:b'compacted'}, mode=0o755))
            self.assertEqual(rejected['unclassified'], [name])

    def test_build_output_count_change_and_backend_digest_are_exactly_bound(self):
        old_inventory, new_inventory = production_inventories()
        old_count = len(json.loads(old_inventory)['source_inputs'])
        new_count = len(json.loads(new_inventory)['source_inputs'])
        line = lambda count: json.dumps({'production_input_closure':'passed', 'files':count,
                                        'adapter_headers':['stdint.h','stddef.h']})+'\n'
        old_text = 'cargo:rerun-if-changed=/src/fixed\n'+line(old_count)+'cargo:rustc-cfg=fixed\n'
        new_text = old_text.replace(line(old_count), line(new_count))
        old = [{'dep_info':'release/build/bridge/output', 'sha256':gate.sha(old_text.encode()), 'source_inputs':['fixed']}]
        new = [dict(old[0], sha256=gate.sha(new_text.encode()), build_output=new_text)]
        self.assertTrue(gate.cargo_observations(old,new,old_inventory,new_inventory))
        for bad in [new_text+line(new_count), new_text.replace(line(new_count),line(new_count-1)),
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

    def test_product_inventory_keeps_root_headers_and_rejects_loose_or_renamed_members(self):
        def product(root, mode=0o755, loose=False):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as stream:
                info = tarfile.TarInfo(root)
                info.type, info.mode = tarfile.DIRTYPE, mode
                stream.addfile(info)
                info = tarfile.TarInfo('loose.bin' if loose else root+'/runtime.dll')
                info.size = 7
                stream.addfile(info,io.BytesIO(b'runtime'))
            return io.BytesIO(output.getvalue())
        root = gate.LOCK['tool_name']
        original, _ = gate.inventory(product(root),True)
        changed, _ = gate.inventory(product(root,mode=0o700),True)
        self.assertEqual(original['.']['type'],'5')
        self.assertNotEqual(original['.'],changed['.'])
        for invalid in [product('renamed'), product(root,loose=True)]:
            with self.assertRaisesRegex(ValueError,'product root'):
                gate.inventory(invalid,True)
        with self.assertRaisesRegex(ValueError,'directory'):
            gate.inventory(io.BytesIO(archive({root:b'loose runtime'})),True)


    def test_member_owners_extended_attributes_and_hardlink_roots(self):
        def packed(uname='', gname='', pax=None, target=None):
            out = io.BytesIO()
            with tarfile.open(fileobj=out, mode='w') as stream:
                item = tarfile.TarInfo(gate.LOCK['tool_name']+'/file')
                item.uname, item.gname, item.pax_headers = uname, gname, pax or {}
                stream.addfile(item)
                if target:
                    link = tarfile.TarInfo(gate.LOCK['tool_name']+'/alias')
                    link.type, link.linkname = tarfile.LNKTYPE, target
                    stream.addfile(link)
            return io.BytesIO(out.getvalue())
        plain, _ = gate.inventory(packed(), True)
        for kw in [dict(uname='other'), dict(gname='other'), dict(pax={'custom.key':'value'})]:
            changed, _ = gate.inventory(packed(**kw), True)
            self.assertNotEqual(plain, changed)
        for attrs in [{'SCHILY.xattr.user.test':'value'}, {'SCHILY.acl.access':'value'},
                      {'LIBARCHIVE.xattr.user.test':'value'}]:
            with self.assertRaisesRegex(ValueError, 'attributes'):
                gate.inventory(packed(pax=attrs), True)
        gate.inventory(packed(target=gate.LOCK['tool_name']+'/file'), True)
        with self.assertRaisesRegex(ValueError, 'crosses product root'):
            gate.inventory(packed(target='ELSEWHERE/file'), True)

    def test_nested_exceptions_are_scoped_and_added_files_have_git_headers(self):
        before = archive({'README.md':b'old'})
        with mock.patch.object(gate.subprocess, 'check_output', return_value=b'compacted'):
            result = gate.nested_changes('sources/dxvk-nvapi-prepared.tar.gz', before,
                                         archive({'README.md':b'compacted'}))
            self.assertEqual(result['unclassified'], ['README.md'])
        path = 'wine/zluda_wine/build-pilot.sh'
        result = gate.nested_changes('sources/wine-prepared.tar.gz', archive({path:b'old'}), archive({}))
        self.assertEqual(result['unclassified'], [path])
        with mock.patch.object(gate.subprocess, 'check_output', return_value=b'compacted'), \
             mock.patch.object(gate, 'git', side_effect=['100644 blob '+'a'*40+'\tREADME.md', '123']):
            result = gate.nested_changes('sources/zluda-wine.tar.gz', archive({}), archive({'README.md':b'compacted'},mode=0o4755))
            self.assertEqual(result['unclassified'], ['README.md'])

    def test_commit_epoch_never_applies_to_external_extra_or_release_tarball(self):
        def timed(files, epoch, commit='a'*40):
            out = io.BytesIO()
            with tarfile.open(fileobj=out, mode='w', pax_headers={'comment':commit}) as stream:
                for path in files:
                    item = tarfile.TarInfo(path)
                    item.mtime = epoch
                    stream.addfile(item)
            return out.getvalue()
        for name, paths in [('sources/numactl-2.0.18.tar.gz',['README']),
                            ('sources/zluda-wine.tar.gz',['external/prepared-wine-sdk/lib/ntdll.so']),
                            ('sources/dxvk-nvapi-prepared.tar.gz',['README'])]:
            result=gate.nested_changes(name,timed(paths,0),timed(paths,123))
            self.assertEqual(result['unclassified'],paths)
        contents={'sources/zluda-wine.tar.gz.json':json.dumps({'extra_submodule_files':{'ext/input.h':'fixed'}}).encode()}
        with mock.patch.object(gate,'git',return_value='tracked.rs'):
            result=gate.nested_changes('sources/zluda-wine.tar.gz',timed(['ext/input.h'],0),timed(['ext/input.h'],123),contents,contents)
            self.assertEqual(result['unclassified'],['ext/input.h'])
        result=gate.nested_changes('sources/wine-prepared.tar.gz',timed(['source'],0),timed(['source'],123))
        self.assertEqual(result['unclassified'],[])
        # Multiple new epochs cannot be attributed to one Git commit.
        before=timed(['a','b'],0)
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',pax_headers={'comment':'b'*40}) as stream:
            for path,epoch in [('a',123),('b',124)]:
                item=tarfile.TarInfo(path);item.mtime=epoch;stream.addfile(item)
        result=gate.nested_changes('sources/wine-prepared.tar.gz',before,out.getvalue())
        self.assertEqual(result['unclassified'],['a','b'])


    def test_partial_wine_epoch_change_is_unclassified(self):
        def packed(epochs):
            out=io.BytesIO()
            with tarfile.open(fileobj=out,mode='w',pax_headers={'comment':'a'*40}) as stream:
                for name,epoch in epochs.items():
                    item=tarfile.TarInfo(name);item.mtime=epoch;stream.addfile(item)
            return out.getvalue()
        result=gate.nested_changes('sources/wine-prepared.tar.gz',packed({'a':1,'b':1}),packed({'a':2,'b':1}))
        self.assertEqual(result['unclassified'],['a'])

    def test_patch_message_requires_a_real_diff_body(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);relative='patches/example.patch';path=root/relative
            path.parent.mkdir();path.write_bytes(b'Subject: new\n')
            with mock.patch.object(gate,'PROTON_ROOT',root):
                self.assertIsNone(gate.patch_message_change(relative,b'Subject: old\n',path.read_bytes()))
                body=b'\ndiff --git a/a b/a\nbody'
                path.write_bytes(b'Subject: new\n'+body)
                self.assertIsNotNone(gate.patch_message_change(relative,b'Subject: old\n'+body,path.read_bytes()))
                self.assertIsNone(gate.patch_message_change(relative,b'Subject: old\n'+body+b'changed',path.read_bytes()))


    def test_actual_license_path_and_nested_inventory_content_are_bound(self):
        before, after = production_inventories()
        real=['licenses/zluda-wine/LICENSES/PRODUCTION-INPUTS.json',
              'licenses/zluda-wine/wine/zluda_wine/LICENSES/PRODUCTION-INPUTS.json']
        for name in real:
            self.assertIsNotNone(gate.support_change(name,before,after,{}))
            self.assertIsNone(gate.support_change(name,before,after+b'forged',{}))
        self.assertIsNone(gate.support_change('licenses/zluda-wine/unknown/LICENSES/PRODUCTION-INPUTS.json',before,after,{}))
        name='wine/zluda_wine/LICENSES/PRODUCTION-INPUTS.json'
        entries,content=gate.inventory(io.BytesIO(archive({name:before})),False)
        self.assertEqual(content[name],before)
        result=gate.nested_changes('sources/zluda-wine.tar.gz',archive({name:before}),archive({name:after}))
        self.assertEqual(result['unclassified'],[])

    def test_removed_directory_requires_every_leaf_on_removal_list(self):
        directory='wine/w1-fixtures/tools/cuda-count'
        reviewed=directory+'/count.c'
        def packed(extra=False):
            output=io.BytesIO()
            with tarfile.open(fileobj=output,mode='w') as stream:
                info=tarfile.TarInfo(directory);info.type=tarfile.DIRTYPE;stream.addfile(info)
                stream.addfile(tarfile.TarInfo(reviewed))
                if extra:stream.addfile(tarfile.TarInfo(directory+'/unlisted.c'))
            return output.getvalue()
        result=gate.nested_changes('sources/zluda-wine.tar.gz',packed(),archive({}))
        self.assertEqual(result['unclassified'],[])
        result=gate.nested_changes('sources/zluda-wine.tar.gz',packed(True),archive({}))
        self.assertIn(directory,result['unclassified'])
        self.assertIn(directory+'/unlisted.c',result['unclassified'])

    def test_wine_overlay_inputs_bind_exact_selected_derivation(self):
        name='provenance/WINE-OVERLAY'
        recipe=b'admitted recipe'
        before=b'rev=old\ninputs=old-inputs\nbase_inputs=unchanged\nconfigure_recipe_sha256=old\n'
        after=('rev='+gate.PROFILE['source_commit']+'\ninputs=selected-inputs\nbase_inputs=unchanged\nconfigure_recipe_sha256='+gate.sha(recipe)+'\n').encode()
        contents={'provenance/WINE-BUILD-RECIPE.mk':recipe}
        for candidate in [after,after.replace(b'selected-inputs',b'forged'),
                          after+b'inputs=selected-inputs\n',after.replace(b'unchanged',b'forged')]:
            with mock.patch.object(gate,'wine_inputs',side_effect=['old-inputs','selected-inputs']):
                result=gate.support_change(name,before,candidate,contents)
            self.assertEqual(result is not None,candidate==after)
