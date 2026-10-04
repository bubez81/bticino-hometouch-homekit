"""Install the tested Speex correction only; retain a rollback manifest."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

stage=Path(__file__).resolve().parent
base=Path('/opt/bticino-sniffer')
bridge=Path('/private/tmp/bticino-child')
ffmpeg='/usr/local/lib/node_modules/homebridge-unifi-protect/node_modules/ffmpeg-for-homebridge/ffmpeg'
if os.geteuid()!=0: raise SystemExit('sudo required')
for command in ('incoming_status','status'):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3); sock.connect('/tmp/bticino-hometouch.sock')
        sock.sendall((json.dumps({'command':command})+'\n').encode())
        response=json.loads(sock.makefile('rb').readline())
        if response.get('incoming') or response.get('state')=='calling':
            raise SystemExit('Call active; installation postponed')
encoders=subprocess.check_output([ffmpeg,'-hide_banner','-encoders'],stderr=subprocess.DEVNULL).decode()
if 'libspeex' not in encoders or 'libopus' not in encoders: raise SystemExit('Required audio encoders missing')
compile((stage/'bticino_audio_offer.py').read_bytes(),'audio_offer','exec')
for name in ('incoming-call.js','two-way-audio.js'):
    subprocess.run(['/usr/local/bin/node','--check',str(stage/name)],check=True)
config=json.loads((bridge/'config.json').read_text())
matches=[a for a in config.get('accessories',[]) if a.get('accessory')=='BTicinoHOMETOUCH']
if len(matches)!=1: raise SystemExit('Unexpected test bridge configuration')
matches[0]['audioFfmpegPath']=ffmpeg
targets=[base/'bticino_audio_offer.py',bridge/'plugin/two-way-audio.js',bridge/'plugin/incoming-call.js',bridge/'config.json']
backup=Path(tempfile.mkdtemp(prefix='speex-audio-',dir=str(base/'backups')))
for i,target in enumerate(targets): shutil.copy2(str(target),str(backup/str(i)))
(backup/'manifest.json').write_text(json.dumps([str(t) for t in targets]))
try:
    for target in targets[:-1]: shutil.copyfile(str(stage/target.name),str(target))
    targets[-1].write_text(json.dumps(config,indent=2)+'\n')
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'],check=True)
except Exception:
    for i,target in enumerate(targets): shutil.copy2(str(backup/str(i)),str(target))
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'])
    raise
print('SPEEX_AUDIO_INSTALLED; BACKUP='+str(backup))
