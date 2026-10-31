#!/usr/bin/env python3
"""License staging, component provenance and GE release assembly."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

from inputs import HERE, LOCK, components_for_part, git, manifest, sha256, verify_manifest
import wine_scope
import cuda_package
import component_patches
import reuse_components


def environment(name):
    return Path(os.environ[name]).resolve()


def license_name(name):
    return name.lower().endswith('-mit.txt') or bool(re.match(
        r'^(licen[sc]e|copying|copyright|notice)([._-].*|$)', name, re.I))


def collect_licenses(source, target, required=True):
    found = []
    for directory, dirs, files in os.walk(source):
        dirs[:] = [d for d in dirs if d not in ('.git', 'target', 'build') and
                   not (Path(directory) / d).is_symlink()]
        for name in files:
            if license_name(name) or 'LICENSES' in Path(directory).relative_to(source).parts or name.endswith('.license'):
                path = Path(directory) / name
                if path.is_file():
                    out = target / path.relative_to(source)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, out)
                    found.append(path.relative_to(source).as_posix())
    if not found and required:
        raise ValueError('no license/copyright notices in ' + str(source))
    return sorted(found)


def collect_bridge_notices(source, dest):
    crate = source / 'wine/zluda_wine'
    target = dest / 'licenses/zluda-wine'
    collect_licenses(crate, target)
    for name, expected in LOCK['wine_bridge']['provenance_bundle'].items():
        if sha256(crate / name) != expected:
            raise ValueError('bridge provenance bundle differs: ' + name)
    for name in ['LICENSE-MIT', 'LICENSE-APACHE']:
        shutil.copyfile(source / name, target / name)
    shutil.copyfile(crate / 'Cargo.lock', target / 'Cargo.lock')
    (dest / 'ZLUDA-WINE-NOTICE.md').write_text(
        'The zluda_wine production bridge is MIT OR Apache-2.0. See '
        'licenses/zluda-wine/LICENSE-MIT, LICENSE-APACHE and LICENSES/PROVENANCE.md.\n'
        'Wine and Proton remain under their own terms. The retained C import '
        'and tests/lgpl are reference/test-only LGPL material, not production '
        'inputs and not relicensed. The complete source archive preserves their '
        'source, notices and recipes. Cargo dependencies retain their own notices.\n')


def sdk():
    build, cache, dest, sdk = map(environment, ['BUILD_ROOT', 'DOWNLOAD_ROOT', 'DESTDIR', 'SDK_ROOT'])
    root = build / 'sdk-extracted'
    if root.exists() or sdk.exists():
        raise ValueError('SDK extraction directories must be new')
    packages = []
    owned = set()
    for item in LOCK['downloads']:
        if item.get('group') != 'rocm':
            continue
        archive = cache / item['url'].rsplit('/', 1)[1]
        if not (sha256(archive) == item['sha256']):
            raise ValueError(item['id'])
        package = build / 'packages' / item['id']
        package.mkdir(parents=True)
        subprocess.run(['dpkg-deb', '-x', str(archive), str(package)], check=True)
        usage = item['usage']
        if usage not in ('build', 'runtime'):
            raise ValueError('unknown ROCm package usage: ' + item['id'])
        payload = item['runtime_files']
        if (usage == 'build' and payload) or (usage == 'runtime' and not payload):
            raise ValueError('invalid ROCm runtime ownership: ' + item['id'])
        for name in payload:
            if name not in LOCK['rocm_runtime'] or name in owned:
                raise ValueError('unexpected/duplicate runtime owner: ' + name)
            if not (package / 'opt/rocm-7.2.4/lib' / name).is_file():
                raise ValueError('missing declared runtime file in ' + item['id'] + ': ' + name)
            owned.add(name)
        notices = collect_licenses(package, dest / 'licenses/rocm' / item['id'], required=usage == 'runtime')
        packages.append(dict(item, notices=notices))
        shutil.copytree(package, root, dirs_exist_ok=True, symlinks=True)
    if owned != set(LOCK['rocm_runtime']):
        raise ValueError('ROCm runtime ownership is incomplete')
    source = root / 'opt/rocm-7.2.4'
    shutil.copytree(source, sdk, symlinks=True)
    for real, aliases in LOCK['rocm_runtime'].items():
        path = sdk / 'lib' / real
        with path.open('rb') as stream:
            if not (stream.read(4) == b'\x7fELF'):
                raise ValueError(real)
        out = dest / 'files/lib64' / real
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out)
        out.chmod(0o755)
        for alias in aliases:
            (out.parent / alias).symlink_to(real)
    provenance = dest / 'provenance'
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / 'ROCM-PACKAGES.json').write_text(json.dumps(packages, indent=2) + '\n')


def metadata(part):
    source, dest = environment('SOURCE_ROOT'), environment('DESTDIR')
    names = components_for_part(part)
    for name in names:
        collect_licenses(source / name, dest / 'licenses' / name)
    if part in ('bridges', 'all'):
        collect_bridge_notices(source / 'zluda', dest)
    if part in ('zluda', 'all'):
        # Autogenerated Cargo output is excluded; retain the authoritative lock.
        collect_licenses(environment('BUILD_ROOT') / 'numactl-2.0.18', dest / 'licenses/numactl')
        shutil.copyfile(source / 'zluda/Cargo.lock', dest / 'licenses/zluda/Cargo.lock')
    if part in ('zluda', 'bridges', 'all'):
        cargo = Path(os.environ['CARGO_HOME'])
        collect_licenses(cargo / 'registry/src', dest / 'licenses/cargo/registry')
        if (cargo / 'git/checkouts').exists():
            collect_licenses(cargo / 'git/checkouts', dest / 'licenses/cargo/git')
    prov = dest / 'provenance'
    prov.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / 'inputs.lock.json', prov / 'inputs.lock.json')
    recipes = dest / 'sources/overlay-recipes'
    shutil.copytree(HERE, recipes, ignore=shutil.ignore_patterns('__pycache__'))
    record = {'format': 'zluda-overlay-provenance-1', 'part': part,
              'proton': git(source, 'rev-parse', 'HEAD'),
              'input_lock_sha256': sha256(HERE / 'inputs.lock.json'),
              'components': {n: LOCK['components'][n] for n in names},
              'component_patches': {n: LOCK['component_patches'][n] for n in names
                                    if n in LOCK.get('component_patches', {})},
              'wine_bridge': LOCK['wine_bridge'],
              'sdk_image': LOCK['sdk_image'], 'rust': LOCK['rust_version'],
              'llvm': LOCK['llvm'], 'architecture': LOCK['architecture'],
              'reproducibility': 'Pinned recipe; bitwise reproducibility and target-runtime/GPU qualification pending'}
    (prov / (part + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    versions = subprocess.check_output(['dpkg-query', '-W']).decode()
    (prov / (part + '-build-packages.txt')).write_text(versions)
    commands = ['cc', 'x86_64-w64-mingw32-gcc', 'i686-w64-mingw32-gcc', 'cmake', 'meson', 'ninja']
    with (prov / (part + '-tools.txt')).open('w') as out:
        for command in commands:
            subprocess.run([command, '--version'], stdout=out, stderr=subprocess.STDOUT, check=True)
    if part in ('zluda', 'all'):
        with (prov / 'cargo-metadata.json').open('wb') as out:
            subprocess.run(['cargo', 'metadata', '--offline', '--locked', '--format-version=1'],
                           cwd=source / 'zluda', stdout=out, check=True)
    # This is deliberately a minimal inventory, not an asserted SPDX document.
    sbom = {'format': 'zluda-overlay-sbom-1', 'scope': 'overlay; GE archive retains its own licenses',
            'production_bridge_license': LOCK['wine_bridge']['license'],
            'reference_material': LOCK['wine_bridge']['test_license'],
            'sources': record['components'], 'binary_and_source_inputs': LOCK['downloads'],
            'license_files': sorted(p.relative_to(dest).as_posix() for p in (dest / 'licenses').rglob('*') if p.is_file()),
            'cargo_lock': LOCK['cargo_lock_sha256']}
    (prov / (part + '-SBOM.json')).write_text(json.dumps(sbom, indent=2) + '\n')


def required_payload():
    required = ['proton', 'proton_zluda.py', 'files/lib64/libcuda.so.1', 'files/lib64/libcuda.so',
                'files/lib64/libnuma.so.1',
                *['files/lib/wine-cuda/zluda/' + name for name in cuda_package.MODULES],
                *['files/lib/wine-cuda/stub/' + arch + '/nvcuda.dll' for arch in cuda_package.policy.ARCHES.values()],
                'files/lib/wine-cuda/stubs.json',
                'files/lib/wine/nvapi/x86_64-windows/nvapi64.dll',
                'files/lib/wine/nvapi/x86_64-windows/nvofapi64.dll',
                'files/lib/wine/nvapi/i386-windows/nvapi.dll']
    required += ['files/lib64/' + p for real, aliases in LOCK['rocm_runtime'].items()
                 for p in [real, *aliases]]
    required += [f'files/lib/wine/dxvk/{arch}/{name}.dll'
                 for arch in ['x86_64-windows', 'i386-windows']
                 for name in ['d3d11', 'dxgi', 'd3d10core', 'd3d9', 'd3d8']]
    required += ['ZLUDA-WINE-NOTICE.md', 'licenses/zluda-wine/LICENSE-MIT',
                 'licenses/zluda-wine/LICENSE-APACHE', 'provenance/WINE-CALLBACK-ATTESTATION.json']
    required += ['licenses/zluda-wine/' + name for name in LOCK['wine_bridge']['provenance_bundle']]
    return required


def extract(archive, target, ge_base=False):
    target.mkdir(parents=True)
    def base_filter(member, destination):
        # Preserve GE's prefix-template symlinks as inert links, but never
        # write a later member through an external link. Overlay archives use
        # the stricter data filter and cannot introduce external links.
        if member.issym():
            return tarfile.tar_filter(member, destination)
        return tarfile.data_filter(member, destination)
    with tarfile.open(archive) as stream:
        stream.extractall(target, filter=base_filter if ge_base else 'data')


def assemble(overlays):
    source, cache, dest, build = map(environment, ['SOURCE_ROOT', 'DOWNLOAD_ROOT', 'DESTDIR', 'BUILD_ROOT'])
    component_commit = reuse_components.expected_commit(source)
    reuse = None
    if os.environ.get('REUSE_COMPONENTS_COMMIT'):
        reuse = json.loads(Path(os.environ['COMPONENT_REUSE_RECEIPT']).read_text())
        if reuse['commit'] != component_commit:
            raise ValueError('component reuse receipt commit mismatch')
        reuse['downloaded_archives'] = {p.name: sha256(p) for p in sorted(overlays.glob('*.tar.gz'))}
    # Keep source submodules at their upstream gitlinks. Runtime Python files
    # come from the same locked GE-plus-ZLUDA patch series as a full GE build.
    pf = build / 'patched/protonfixes'
    component_patches.prepare(source, pf, 'protonfixes', LOCK)
    ge = next(x for x in LOCK['downloads'] if x['id'] == 'ge')
    archive = cache / ge['url'].rsplit('/', 1)[1]
    if not (sha256(archive) == ge['sha256']):
        raise ValueError("overlay.py integrity gate failed: sha256(archive) == ge['sha256']")
    if dest.exists():
        raise ValueError('DESTDIR already exists')
    dest.parent.mkdir(parents=True, exist_ok=True)
    unpack = build / 'ge-unpacked'
    extract(archive, unpack, ge_base=True)
    children = list(unpack.iterdir())
    if not (len(children) == 1 and children[0].is_dir()):
        raise ValueError('unexpected GE archive layout')
    shutil.move(str(children[0]), dest)
    cuda_package.prepare_stubs(dest)
    template_links = {p.relative_to(dest).as_posix(): os.readlink(p)
                      for p in dest.rglob('*') if p.is_symlink() and
                      (not p.resolve().is_relative_to(dest) or not p.exists())}
    (dest / 'provenance').mkdir(exist_ok=True)
    (dest / 'provenance/GE-TEMPLATE-LINKS.json').write_text(
        json.dumps(template_links, sort_keys=True, indent=2) + '\n')
    for part in ['zluda', 'bridges']:
        overlay = build / ('verified-' + part)
        extract(overlays / (part + '.tar.gz'), overlay)
        verify_manifest(overlay)
        record = json.loads((overlay / 'provenance' / (part + '.json')).read_text())
        if not (record['part'] == part and record['proton'] == component_commit):
            raise ValueError("overlay.py integrity gate failed: record['part'] == part and record['proton'] == git(source, 'rev-parse', 'HEAD')")
        if not (record['input_lock_sha256'] == sha256(HERE / 'inputs.lock.json')):
            raise ValueError("overlay.py integrity gate failed: record['input_lock_sha256'] == sha256(HERE / 'inputs.lock.json')")
        (overlay / 'MANIFEST').unlink()
        (overlay / 'SYMLINKS.json').unlink()
        shutil.copytree(overlay, dest, dirs_exist_ok=True, symlinks=True)
    scope = dest / 'provenance/WINE-SCOPE.json'
    if reuse:
        (dest / 'provenance/COMPONENT-REUSE.json').write_text(json.dumps(reuse, indent=2) + '\n')
        shutil.copytree(HERE, dest / 'sources/overlay-recipes', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__'))
    wine_scope.verify(dest, json.loads(scope.read_text()))
    for name in ['proton', 'proton_zluda.py']:
        (dest / name).write_bytes(subprocess.check_output(['git', '-C', str(source), 'show', 'HEAD:' + name]))
    (dest / 'proton').chmod(0o755)
    base = git(source, 'rev-parse', LOCK['ge_source'] + ':protonfixes')
    for line in git(pf, 'diff', '--cached', '--name-status', '--no-renames', base).splitlines():
        status, name = line.split('\t', 1)
        path = dest / 'protonfixes' / name
        if not (path.resolve().is_relative_to((dest / 'protonfixes').resolve())):
            raise ValueError("overlay.py integrity gate failed: path.resolve().is_relative_to((dest / 'protonfixes').resolve())")
        if status == 'D':
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(subprocess.check_output(['git', '-C', str(pf), 'show', ':' + name]))
            mode = git(pf, 'ls-files', '--stage', '--', name).split()[0]
            path.chmod(0o755 if mode == '100755' else 0o644)
    patches_record = component_patches.stage_sources(source, pf, 'protonfixes', dest, LOCK)
    for path in (dest / 'protonfixes').rglob('*.pyc'):
        path.unlink()
    vdf = dest / 'compatibilitytool.vdf'
    text, n = re.subn(r'(\n\s*)"[^"]+"(\s*// Internal name)',
                      lambda m: m[1] + '"' + LOCK['tool_name'] + '"' + m[2], vdf.read_text(), count=1)
    if not (n == 1):
        raise ValueError('unknown compatibilitytool.vdf layout')
    text, n = re.subn(r'("display_name"\s*)"[^"]*"', r'\1"GE-Proton11-7 ZLUDA"', text, count=1)
    if not (n == 1):
        raise ValueError('overlay.py integrity gate failed: n == 1')
    vdf.write_text(text)
    cuda_package.remove_canonical(dest)
    cuda_package.materialize_template(dest)
    cuda_package.audit(dest)
    for name in required_payload():
        if not ((dest / name).is_file() and (dest / name).stat().st_size):
            raise ValueError(name)
    provenance = {'format': 'zluda-overlay-release-1', 'proton': git(source, 'rev-parse', 'HEAD'),
                  'component_build_proton': component_commit, 'component_reuse': reuse,
                  'ge': ge, 'components': LOCK['components'], 'llvm': LOCK['llvm'],
                  'component_patches': LOCK['component_patches'],
                  'wine_bridge': LOCK['wine_bridge'],
                  'protonfixes_prepared_tree': patches_record['prepared_tree'],
                  'input_lock_sha256': sha256(HERE / 'inputs.lock.json'),
                  'qualification': 'CI assembly is not target-runtime or GPU qualification'}
    (dest / 'provenance/PROVENANCE.json').write_text(json.dumps(provenance, indent=2) + '\n')
    sbom = {'format': 'zluda-overlay-sbom-1', 'base': ge,
            'component_inventories': ['zluda-SBOM.json', 'bridges-SBOM.json'],
            'licenses': 'licenses/', 'sources': 'sources/'}
    (dest / 'provenance/SBOM.json').write_text(json.dumps(sbom, indent=2) + '\n')
    # Names, aliases and installed bytes are recorded before archive compression.
    sizes = {'installed_regular_bytes': sum(p.stat().st_size for p in dest.rglob('*') if p.is_file() and not p.is_symlink()),
             'source_bytes': sum(p.stat().st_size for p in (dest / 'sources').rglob('*') if p.is_file() and not p.is_symlink()),
             'unique_content_bytes': sum({sha256(p): p.stat().st_size for p in dest.rglob('*') if p.is_file() and not p.is_symlink()}.values())}
    (dest / 'provenance/SIZES.json').write_text(json.dumps(sizes, indent=2) + '\n')
    manifest(dest)
    verify_manifest(dest)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['sdk', 'metadata', 'assemble'])
    parser.add_argument('--part', choices=['all', 'zluda', 'bridges'], default='all')
    parser.add_argument('--overlays', type=Path)
    args = parser.parse_args()
    if args.command == 'sdk':
        sdk()
    elif args.command == 'metadata':
        metadata(args.part)
    else:
        assemble(args.overlays)


if __name__ == '__main__':
    main()
