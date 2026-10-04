"""Guarded update of test bridge only; never send an opening command."""
import hashlib
import json
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

stage = Path(__file__).resolve().parent
base = Path('/opt/bticino-sniffer')
plugin = Path('/opt/bticino-homebridge/plugin')
expected = {
    'call-unlock.js': 'a91c1aac3f23d21e54ba4a26bf45e479c1bec2cb5f2681d39de79b58861dea51',
    'stream.js': '90c8ae53dc866323c207d60d5b1df6de5a97751cb527cff2e2f344708d427458',
    'incoming-call.js': '62740c59bd5fc4a817d3e22ac4ec2414d62a7d797cf51fddb125c6a7364483f3',
}
for name, digest in expected.items():
    if hashlib.sha256((plugin/name).read_bytes()).hexdigest() != digest:
        raise SystemExit('Installed version changed: '+name)
    subprocess.run(['/usr/local/bin/node', '--check', str(stage/name)], check=True)
listener = base/'listener.py'
old = """                if not request['enabled']:
                    attachment.allowed = False
                    return {'ok': True}
"""
new = """                if not request['enabled']:
                    attachment.allowed = False
                    if request.get('answer') is True:
                        dialog.answer(owner, capture.answer_sdp, audio_ready=True)
                        self.publish_incoming_state()
                    return {'ok': True}
"""
source = listener.read_text()
if source.count(old) != 1:
    raise SystemExit('Listener version mismatch')
updated = source.replace(old, new)
compile(updated, str(listener), 'exec')
for command in ('incoming_status', 'status'):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect('/tmp/bticino-hometouch.sock')
        sock.sendall((json.dumps({'command': command})+'\n').encode())
        result = json.loads(sock.makefile('rb').readline())
        if result.get('incoming') or result.get('state') == 'calling':
            raise SystemExit('Call active; update postponed')
backup = Path(tempfile.mkdtemp(prefix='homekit-call-fix-', dir=str(base/'backups')))
targets = [listener]+[plugin/name for name in expected]
for target in targets:
    shutil.copy2(target, backup/target.name)
labels = ('io.github.bubez81.bticino-homebridge', 'io.github.bubez81.bticino-hometouch')
try:
    listener.write_text(updated)
    for name in expected:
        shutil.copyfile(stage/name, plugin/name)
    for label in reversed(labels):
        subprocess.run(['/bin/launchctl', 'kickstart', '-k', 'system/'+label], check=True)
except Exception:
    for target in targets:
        shutil.copy2(backup/target.name, target)
    for label in reversed(labels):
        subprocess.run(['/bin/launchctl', 'kickstart', '-k', 'system/'+label])
    raise
print('INSTALLED BACKUP='+str(backup))
