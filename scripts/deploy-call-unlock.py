"""Scoped deployment on the test host. Never issues an opening command."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import socket
import subprocess
import tempfile
import time

stage=Path(__file__).resolve().parent
base=Path('/opt/bticino-sniffer')
bridge=Path('/opt/bticino-homebridge')
label='io.github.bubez81.bticino-homebridge'
plist=Path('/Library/LaunchDaemons/'+label+'.plist')
if os.geteuid()!=0: raise SystemExit('sudo required')
if plist.exists(): raise SystemExit('Service already exists; inspect before updating')
for command in ('incoming_status','status'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3);sock.connect('/tmp/bticino-hometouch.sock')
        sock.sendall((json.dumps({'command':command})+'\n').encode())
        response=json.loads(sock.makefile('rb').readline())
        if response.get('incoming') or response.get('state')=='calling':
            raise SystemExit('Call active; installation postponed')
pid=None
with socket.socket() as probe:
    probe.settimeout(2)
    if probe.connect_ex(('127.0.0.1',51991))==0:
        raise SystemExit('Bridge port is occupied; inspect before deployment')
index=bridge/'plugin/index.js'
if hashlib.sha256(index.read_bytes()).hexdigest()!='3a0219e872263b280837300dd4cfa0d7b00fccef49068ffab47374fe72f34d9c':
    raise SystemExit('Plugin changed; refusing overwrite')
listener=base/'listener.py'
text=listener.read_text()
old='from bticino_homekit_call import CallCommands, MediaAttachment'
if text.count(old)!=1 or 'open_current_call' in text: raise SystemExit('Unexpected listener version')
text=text.replace(old,old+'\nfrom bticino_call_unlock import open_current_call')
old="        command = request['command']\n"
if text.count(old)!=1: raise SystemExit('Unexpected handler')
text=text.replace(old,old+"        if command == 'open_incoming':\n            return open_current_call(self, owner, CONFIG.get('incoming_unlock', False), DOMAIN)\n")
compile(text,str(listener),'exec')
ipc=base/'bticino_ipc.py'
ipc_text=ipc.read_text()
old="('attach_incoming', 'answer_incoming', 'release_incoming')"
if ipc_text.count(old)!=1: raise SystemExit('Unexpected IPC version')
ipc_text=ipc_text.replace(old,"('attach_incoming', 'answer_incoming', 'release_incoming', 'open_incoming')")
compile(ipc_text,str(ipc),'exec')
compile((stage/'bticino_call_unlock.py').read_text(),'unlock','exec')
for name in ('index.js','call-unlock.js'):
    subprocess.run(['/usr/local/bin/node','--check',str(stage/name)],check=True)
lc=json.loads((base/'config.json').read_text());lc['incoming_unlock']=True
bc=json.loads((bridge/'config.json').read_text())
accessories=[a for a in bc.get('accessories',[]) if a.get('accessory')=='BTicinoHOMETOUCH']
if len(accessories)!=1:raise SystemExit('Unexpected accessory configuration')
accessories[0]['enableCallUnlock']=True
changes={listener:text.encode(),ipc:ipc_text.encode(),
    base/'bticino_call_unlock.py':(stage/'bticino_call_unlock.py').read_bytes(),
    index:(stage/'index.js').read_bytes(),
    bridge/'plugin/call-unlock.js':(stage/'call-unlock.js').read_bytes(),
    base/'config.json':(json.dumps(lc,indent=2)+'\n').encode(),
    bridge/'config.json':(json.dumps(bc,indent=2)+'\n').encode()}
backup=Path(tempfile.mkdtemp(prefix='call-unlock-',dir=str(base/'backups')))
manifest=[]
for i,target in enumerate(changes):
    exists=target.exists();manifest.append({'path':str(target),'existed':exists,'backup':str(i)})
    if exists:shutil.copy2(str(target),str(backup/str(i)))
(backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
service={'Label':label,'ProgramArguments':['/usr/local/bin/homebridge','-U',str(bridge),'-P',str(bridge/'plugin'),'--strict-plugin-resolution','-Q'],
    'WorkingDirectory':str(bridge),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
    'EnvironmentVariables':{'PATH':'/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin','BTICINO_MAX_WIDTH':'640'},
    'StandardOutPath':str(bridge/'stdout.log'),'StandardErrorPath':str(bridge/'stderr.log')}
try:
    for target,data in changes.items():
        target.write_bytes(data)
        os.chmod(target,0o600 if str(target).startswith(str(base)) or target.name=='config.json' else 0o644)
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'],check=True)
    plist.write_bytes(plistlib.dumps(service));os.chmod(plist,0o644)
    subprocess.run(['/bin/launchctl','bootstrap','system',str(plist)],check=True)
except Exception:
    if plist.exists():
        subprocess.run(['/bin/launchctl','bootout','system/'+label],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        plist.unlink()
    for entry in manifest:
        target=Path(entry['path'])
        if entry['existed']:shutil.copy2(str(backup/entry['backup']),str(target))
        elif target.exists():target.unlink()
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'])
    raise
print('INSTALLED_WITHOUT_OPENING; BACKUP='+str(backup))
