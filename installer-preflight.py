import json,os,pathlib,subprocess,shutil,hashlib
root=pathlib.Path('/work'); tool=pathlib.Path('/tool'); records=[]
base=dict(os.environ)
for k in list(base):
 if k.startswith(('WINE','PROTON_','ZLUDA_','STEAM_','HSA_')) or k in ('LD_PRELOAD','DISPLAY','WAYLAND_DISPLAY'):base.pop(k,None)
base.update(LD_LIBRARY_PATH=':'.join(str(tool/'files/lib'/a) for a in ['wine/x86_64-unix','x86_64-linux-gnu','i386-linux-gnu']),PATH=str(tool/'protonfixes/files/bin')+':'+str(tool/'files/bin')+':/usr/bin:/bin',WINE=str(tool/'files/bin/wine'),WINELOADER=str(tool/'files/bin/wine'),WINESERVER=str(tool/'files/bin/wineserver'),WINEDEBUG='-all',WINEESYNC='0',WINEFSYNC='0',WINENTSYNC='0',WINEDLLPATH=str(tool/'files/lib/wine-cuda/stub')+':'+str(tool/'files/lib/wine'),WINETRICKS_LATEST_VERSION_CHECK='disabled',LD_PRELOAD='',HOME='/work/home',TMPDIR='/work/tmp',LC_ALL='C')
for p in ['home','tmp']: (root/p).mkdir(exist_ok=True)
def call(args,env,name):
 p=subprocess.run(list(map(str,args)),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
 out={'name':name,'code':p.returncode,'stdout':p.stdout.decode(errors='replace'),'stderr':p.stderr.decode(errors='replace')}
 (root/(name+'.json')).write_text(json.dumps(out,indent=2));return out
try:
 for arch in ['wow64','win64']:
  env=dict(base,WINEPREFIX=str(root/('prefix-'+arch)),WINEARCH=arch)
  shutil.copytree(tool/'files/share/default_pfx',env['WINEPREFIX'],symlinks=True)
  devices=pathlib.Path(env['WINEPREFIX'])/'dosdevices';devices.mkdir(exist_ok=True)
  for name,target in [('c:','../drive_c'),('z:','/')]:
   link=devices/name
   if not link.is_symlink():link.symlink_to(target)
  boot=call([env['WINE'],'wineboot','-u'],env,arch+'-boot');assert boot['code']==0,boot
  call([env['WINESERVER'],'-w'],env,arch+'-wait')
  for flag in ['0','1']:
   for inherited in ['yes','no']:
    e=dict(env,PROTON_ENABLE_ZLUDA=flag,PROTON_USE_WOW64='1')
    if inherited=='no':e.pop('WINEARCH',None)
    name=f'{arch}-zluda{flag}-winearch{inherited}'
    r=call([e['WINE'],'cmd.exe','/c','chcp 65001 > nul & echo %AppData%'],e,name+'-cmd')
    w=call([tool/'protonfixes/files/bin/winetricks','--unattended','list-installed'],e,name+'-tricks')
    records.append({'arch':arch,'zluda':flag,'WINEARCH':e.get('WINEARCH'),'cmd':r,'winetricks':w})
  call([env['WINESERVER'],'-k'],env,arch+'-kill');call([env['WINESERVER'],'-w'],env,arch+'-closed')
finally:
 (root/'REPORT.json').write_text(json.dumps(records,indent=2))
print(json.dumps(records,indent=2))
