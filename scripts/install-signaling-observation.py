"""Scoped test-host diagnostic update; no credentials or media keys printed."""
import collections
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

base=Path('/opt/bticino-sniffer')
stage=Path(__file__).resolve().parent
if os.geteuid()!=0:raise SystemExit('sudo required')
for command in ('incoming_status','status'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3);sock.connect('/tmp/bticino-hometouch.sock')
        sock.sendall((json.dumps({'command':command})+'\n').encode());sock.shutdown(socket.SHUT_WR)
        response=json.loads(sock.makefile('rb').readline())
        if response.get('incoming') or response.get('state')=='calling':raise SystemExit('Call active; diagnostic installation postponed')
config=json.loads((base/'config.json').read_text())
config['entrance_signaling_diagnostics']=True
for name in ('bticino_hometouch_listener.py','bticino_signaling_observation.py'):
    compile((stage/name).read_bytes(),name,'exec')
backup=Path(tempfile.mkdtemp(prefix='signaling-observation-',dir=str(base/'backups')))
for name in ('listener.py','config.json'):
    shutil.copy2(str(base/name),str(backup/name))
try:
    shutil.copyfile(str(stage/'bticino_signaling_observation.py'),str(base/'bticino_signaling_observation.py'))
    os.chmod(base/'bticino_signaling_observation.py',0o600)
    shutil.copyfile(str(stage/'bticino_hometouch_listener.py'),str(base/'listener.py'))
    (base/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'],check=True)
except Exception:
    for name in ('listener.py','config.json'):shutil.copy2(str(backup/name),str(base/name))
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'])
    raise
from bticino_signaling_observation import observe
key=(base/'runtime/diagnostic-hmac.key').read_bytes()
counts=collections.Counter();found=0
for path in sorted((base/'logs').glob('*.sip')):
    record=observe(path.read_bytes(),key)
    if record:
        counts[record['method']]+=1
        if record['devices']:
            found+=1;print('ARCHIVE_MATCH',path.name,json.dumps(record))
print('ARCHIVE_METHODS',json.dumps(dict(counts)))
print('ARCHIVE_DEVADDR_MESSAGES',found)
print('BACKUP',backup)
