import json,os,pathlib,subprocess,shutil
root=pathlib.Path('/work');tool=pathlib.Path('/tool');records=[]
(tool/'protonfixes/files/bin/winetricks').rename(tool/'protonfixes/files/bin/winetricks-real')
wrapper=tool/'protonfixes/files/bin/winetricks'
wrapper.write_text('''#!/usr/bin/python3
import os,sys,json,pathlib
out=pathlib.Path(os.environ['INSTALLER_TEST_DIR'])
e={k:v for k,v in os.environ.items() if k.startswith(('WINE','PROTON_','ZLUDA_','LD_','HSA_'))}
(out/'installer-env.json').write_text(json.dumps(e,indent=2))
os.execv(str(pathlib.Path(__file__).with_name('winetricks-real')),['winetricks-real','--unattended','list-installed'])
''');wrapper.chmod(0o755)
for variant in ['automatic','automatic-log','manual-off','late-off']:
 d=root/variant
 for n in ['home','tmp','runtime','compatdata','game','steam','logs']:(d/n).mkdir(parents=True,exist_ok=True)
 env=dict(os.environ)
 for k in list(env):
  if k.startswith(('WINE','PROTON_','ZLUDA_','STEAM_','HSA_')) or k in ('LD_PRELOAD','DISPLAY','WAYLAND_DISPLAY','SteamGameId','SteamAppId','UMU_ID'):env.pop(k,None)
 env.update(HOME=str(d/'home'),TMPDIR=str(d/'tmp'),XDG_RUNTIME_DIR=str(d/'runtime'),PWD=str(d/'game'),STEAM_COMPAT_DATA_PATH=str(d/'compatdata'),STEAM_COMPAT_CLIENT_INSTALL_PATH=str(d/'steam'),STEAM_COMPAT_INSTALL_PATH=str(d/'game'),STEAM_COMPAT_APP_ID='49520',SteamGameId='49520',SteamAppId='49520',UMU_ID='49520',PROTON_NO_ESYNC='1',PROTON_NO_FSYNC='1',PROTON_USE_WINED3D='1',INSTALLER_TEST_DIR=str(d),WINEDEBUG='-all',LC_ALL='C')
 if variant=='automatic-log':env.update(PROTON_LOG='1',PROTON_LOG_DIR=str(d/'logs'))
 if variant in ['manual-off','late-off']:env['PROTON_ENABLE_ZLUDA']='0'
 if variant=='manual-off':env['PROTON_USE_WOW64']='1'
 settings=tool/'user_settings.py'
 if variant=='late-off':settings.write_text("user_settings={'PROTON_ENABLE_ZLUDA':'0','PROTON_USE_WOW64':'1','WINEARCH':'wow64'}\n")
 else:settings.unlink(missing_ok=True)
 log=d/'proton.log'
 with log.open('w') as output:
  p=subprocess.run([str(tool/'proton'),'waitforexitandrun','C:\\windows\\system32\\cmd.exe','/c','echo FIRSTUSE_OK'],env=env,cwd=d/'game',stdout=output,stderr=subprocess.STDOUT,timeout=240)
 text=log.read_text();r={'variant':variant,'returncode':p.returncode,'hook_failed':'PhysX installation failed' in text,'game_output':'FIRSTUSE_OK' in text,'winetricks_logged':(d/'compatdata/pfx/winetricks.log.forced').exists()};records.append(r)
 e=json.loads((d/'installer-env.json').read_text())
 for flag in ['-k','-w']:
  subprocess.run([str(tool/'files/bin/wineserver'),flag],env=e,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
 (root/'REPORT.json').write_text(json.dumps(records,indent=2))
print(json.dumps(records,indent=2))
