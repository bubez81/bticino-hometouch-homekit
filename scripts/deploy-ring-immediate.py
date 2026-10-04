"""Immediate IPC ring and HAP subscription diagnostics; no synthetic rings."""
import json, socket, shutil, subprocess, tempfile
from pathlib import Path
base=Path('/opt/bticino-sniffer');bridge=Path('/opt/bticino-homebridge')
for command in ('incoming_status','status'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
        s.settimeout(3);s.connect('/tmp/bticino-hometouch.sock')
        s.sendall((json.dumps({'command':command})+'\n').encode())
        r=json.loads(s.makefile('rb').readline())
        if r.get('incoming') or r.get('state')=='calling':raise SystemExit('Call active; postponed')
listener=base/'listener.py';index=bridge/'plugin/index.js'
text=listener.read_text()
old='            notify_ipc_ring(self.snapshot, getattr(self, "entrance", None))\n'
anchor='        self.publish_incoming_state()\n        if IPC_MODULE is not None:\n'
if text.count(old)!=1 or text.count(anchor)!=1:raise SystemExit('Listener version mismatch')
text=text.replace(old,'').replace(anchor,anchor+'            notify_ipc_ring(capture.snapshot)\n            log("HOMEKIT IPC: suonata accodata senza attendere lo snapshot")\n')
compile(text,str(listener),'exec')
js=index.read_text()
old='            this.doorbellService.updateCharacteristic(this.api.hap.Characteristic.ProgrammableSwitchEvent, event);'
new='''            const characteristic=this.doorbellService.getCharacteristic(this.api.hap.Characteristic.ProgrammableSwitchEvent);
            this.log.info(`HomeKit ring dispatch: subscribers=${characteristic.subscriptions}`);
            characteristic.sendEventNotification(event);
            this.log.info('HomeKit ring dispatched (phone delivery unconfirmed)');'''
if js.count(old)!=1:raise SystemExit('Plugin version mismatch')
js=js.replace(old,new)
backup=Path(tempfile.mkdtemp(prefix='ring-immediate-',dir=str(base/'backups')))
shutil.copy2(listener,backup/'listener.py');shutil.copy2(index,backup/'index.js')
try:
    listener.write_text(text);index.write_text(js)
    subprocess.run(['/usr/local/bin/node','--check',str(index)],check=True)
    for label in ('io.github.bubez81.bticino-hometouch','io.github.bubez81.bticino-homebridge'):
        subprocess.run(['/bin/launchctl','kickstart','-k','system/'+label],check=True)
except Exception:
    shutil.copy2(backup/'listener.py',listener);shutil.copy2(backup/'index.js',index)
    for label in ('io.github.bubez81.bticino-hometouch','io.github.bubez81.bticino-homebridge'):
        subprocess.run(['/bin/launchctl','kickstart','-k','system/'+label])
    raise
print('RING_UPDATE_INSTALLED BACKUP='+str(backup))
