#!/usr/bin/env python3
"""Real unpatched Wine writer, two temporary module trees and one shared prefix.

No GPU, bridge call or full-candidate qualification. Source functions are loaded
from the actual old/new launchers, as in the existing launcher policy fixtures.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from test_prefix_isolation import functions
from wine_cpu import environment, command, cleanup_server


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(__doc__)
    for n in ('source','baseline','tool','work'):p.add_argument('--'+n,type=Path,required=True)
    args=p.parse_args();args.work.mkdir(parents=True,exist_ok=True)
    if os.geteuid()==0:raise RuntimeError('Wine regression must run as an unprivileged user')
    wine=args.tool/'files/bin/wine';server=args.tool/'files/bin/wineserver'
    work=args.work;prefix=work/'prefix';(work/'tmp').mkdir();(work/'runtime').mkdir(mode=0o700)
    env=environment(args.tool,work,wine,server)
    env.update(WINEDLLPATH=str(args.tool/'files/lib/wine-cuda/stub'),WINEDLLOVERRIDES='mscoree,mshtml=')
    fixed=functions((args.source/'proton').read_text());old=functions(args.baseline.read_text())
    compat=SimpleNamespace(prefix_dir=str(prefix)+'/')
    a=work/'tool-A';b=work/'tool-B'
    records=[]
    def boot(label,initial=False):
        command([wine,'wineboot','-i' if initial else '-u'],env,work/(label+'.log'),120)
        return cleanup_server(server,env,work/('cleanup-'+label))
    boot('initialize',True)
    try:
        for arch,system in [('x86_64-windows','system32'),('i386-windows','syswow64')]:
            module='xinput1_3.dll';original=(args.tool/'files/lib/wine'/arch/module).read_bytes()
            donor=(args.tool/'files/lib/wine'/arch/'version.dll').read_bytes();assert donor!=original
            destination=prefix/'drive_c/windows'/system/module
            assert destination.is_file()
            for tree in (a,b):
                own=tree/'files/lib/wine'/arch/module;own.parent.mkdir(parents=True,exist_ok=True);own.write_bytes(donor)
            # OUTBOUND: A left a shared-prefix symlink or hardlink; B updates it.
            for kind in ('symlink','hardlink'):
                for mode in ('unfixed','fixed'):
                    target=a/'files/lib/wine'/arch/module;target.write_bytes(donor)
                    destination.unlink(missing_ok=True)
                    if kind=='symlink':destination.symlink_to(target)
                    else:os.link(target,destination)
                    if mode=='fixed':fixed['isolate_module_links'](compat,b)
                    private=not destination.is_symlink() and destination.stat().st_ino!=target.stat().st_ino
                    cleanup=boot(arch+'-'+kind+'-'+mode)
                    assert destination.read_bytes()==original, 'no-op Wine update cannot pass'
                    changed=target.read_bytes()!=donor
                    assert changed==(mode=='unfixed'),(arch,kind,mode)
                    if mode=='fixed':assert private
                    records.append(dict(direction='outbound',arch=arch,alias=kind,mode=mode,
                                        foreign_changed=changed,private=private,cleanup=cleanup))
            # INBOUND: next unpatched Wine must have no link into the new tool.
            for mode,ns in [('unfixed',old),('fixed',fixed)]:
                target=b/'files/lib/wine'/arch/module;target.write_bytes(donor)
                template=work/'templates'/arch/module;template.parent.mkdir(parents=True,exist_ok=True)
                template.symlink_to(target)
                destination.unlink()
                os.environ['PROTON_DLL_COPY']='*'
                ns['pfx_copy'](compat,str(template),str(destination))
                private=not destination.is_symlink() and destination.stat().st_ino!=target.stat().st_ino
                cleanup=boot(arch+'-inbound-'+mode)
                assert destination.read_bytes()==original
                changed=target.read_bytes()!=donor;assert changed==(mode=='unfixed')
                if mode=='fixed':assert private
                records.append(dict(direction='inbound',arch=arch,mode=mode,own_changed=changed,
                                    private=private,cleanup=cleanup))
                template.unlink()
            # A linked parent defeats a leaf-only pass. Demonstrate the real
            # writer before requiring refusal from the fixed source function.
            system_dir=destination.parent;foreign=a/('parent-'+system)
            system_dir.rename(foreign);system_dir.symlink_to(foreign)
            (foreign/module).write_bytes(donor)
            cleanup=boot(arch+'-parent-unfixed')
            assert (foreign/module).read_bytes()==original
            (foreign/module).write_bytes(donor)
            try:fixed['isolate_module_links'](compat,b)
            except RuntimeError as e:assert 'linked Windows directory' in str(e)
            else:raise AssertionError('linked parent was followed')
            assert (foreign/module).read_bytes()==donor
            records.append(dict(direction='linked-parent',arch=arch,unfixed_changed=True,
                                fixed_refused=True,cleanup=cleanup))
            system_dir.unlink();foreign.rename(system_dir)
        report=dict(status='passed',scope='module-tree regression with common pinned unpatched Wine writer; not full launcher/game qualification',
                    source_proton_sha256=sha(args.source/'proton'),baseline_proton_sha256=sha(args.baseline),
                    runtime_wine_sha256=sha(wine),runtime_server_sha256=sha(server),cases=records)
        assert len(records)==14
        (work/'RESULT.json').write_text(json.dumps(report,indent=2)+'\n')
        print('PASS: 14 real-Wine cases; unfixed writes reproduced, fixed isolation/refusal verified')
    finally:
        cleanup_server(server,env,work/'final-cleanup')


if __name__=='__main__':main()
