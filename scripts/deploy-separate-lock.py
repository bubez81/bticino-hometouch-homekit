"""Migrate only the test bridge to a separate lock accessory, with rollback."""
import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

stage=Path(__file__).resolve().parent
bridge=Path('/opt/bticino-homebridge')
index=bridge/'plugin/index.js'
config_path=bridge/'config.json'
if hashlib.sha256(index.read_bytes()).hexdigest()!='0b2f0715d5085413fb5ff015722e3318b21d4c631a2262ac645631d6a71b6a26':
    raise SystemExit('Installed index version changed')
subprocess.run(['/usr/local/bin/node','--check',str(stage/'index.js')],check=True)
config=json.loads(config_path.read_text())
cameras=[a for a in config.get('accessories',[]) if a.get('accessory')=='BTicinoHOMETOUCH']
if len(cameras)!=1 or any(a.get('accessory')=='BTicinoCallLock' for a in config['accessories']):
    raise SystemExit('Unexpected accessory configuration')
camera=cameras[0]
if camera.get('enableCallUnlock') is not True:
    raise SystemExit('Unlock was not enabled; refusing to enable implicitly')
camera['separateCallUnlock']=True
config['accessories'].append({'accessory':'BTicinoCallLock','name':'Apri ingresso',
    'cameraName':camera.get('name','BTicino HOMETOUCH'),
    'ipcSocket':camera.get('ipcSocket','/tmp/bticino-hometouch.sock')})
for command in ('incoming_status','status'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3);sock.connect(camera.get('ipcSocket','/tmp/bticino-hometouch.sock'))
        sock.sendall((json.dumps({'command':command})+'\n').encode())
        result=json.loads(sock.makefile('rb').readline())
        if result.get('incoming') or result.get('state')=='calling':
            raise SystemExit('Call active; update postponed')
backup=Path(tempfile.mkdtemp(prefix='separate-lock-',dir='/opt/bticino-sniffer/backups'))
shutil.copy2(index,backup/'index.js');shutil.copy2(config_path,backup/'config.json')
try:
    shutil.copyfile(stage/'index.js',index)
    config_path.write_text(json.dumps(config,indent=2)+'\n')
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-homebridge'],check=True)
except Exception:
    shutil.copy2(backup/'index.js',index);shutil.copy2(backup/'config.json',config_path)
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-homebridge'])
    raise
print('SEPARATE_LOCK_INSTALLED BACKUP='+str(backup))
