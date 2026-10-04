"""Update assumed UI state only; no physical commands."""
import hashlib,json,shutil,socket,subprocess,tempfile
from pathlib import Path
target=Path('/opt/bticino-homebridge/plugin/call-unlock.js')
source=Path(__file__).resolve().parent/'call-unlock.js'
if hashlib.sha256(target.read_bytes()).hexdigest()!='81eec828089dcb8157c0fcef9cdf751c19830f068e9f7aafe49a9c98ec063850':
    raise SystemExit('Installed version changed')
subprocess.run(['/usr/local/bin/node','--check',str(source)],check=True)
for command in ('incoming_status','status'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3);sock.connect('/tmp/bticino-hometouch.sock')
        sock.sendall((json.dumps({'command':command})+'\n').encode())
        response=json.loads(sock.makefile('rb').readline())
        if response.get('incoming') or response.get('state')=='calling':raise SystemExit('Call active; postponed')
backup=Path(tempfile.mkdtemp(prefix='lock-pulse-',dir='/opt/bticino-sniffer/backups'))
shutil.copy2(target,backup/target.name)
try:
    shutil.copyfile(source,target)
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-homebridge'],check=True)
except Exception:
    shutil.copy2(backup/target.name,target)
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-homebridge'])
    raise
print('LOCK_PULSE_INSTALLED BACKUP='+str(backup))
