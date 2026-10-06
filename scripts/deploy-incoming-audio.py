#!/usr/bin/env python3
"""Install the tested incoming-call audio path on the test host's test bridge only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

stage = Path(__file__).resolve().parent
base = Path('/opt/bticino-sniffer')
bridge = Path('/private/tmp/bticino-child')
if os.geteuid() != 0:
    raise SystemExit('Run with sudo')
expected = {
    base/'listener.py': '4bdaef1267d2e10e6dc3cd60b56f49ad9bc0b8d7685f398f9ff01b28c35e3c23',
    base/'bticino_ipc.py': '8f1be50ea7ac12d61f8f554b762b0548349945ef4c37fdeb0389e55305b2c2f3',
}
for target, digest in expected.items():
    if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit('Installed listener changed; refusing to overwrite')
mapping = {'bticino_hometouch_listener.py': base/'listener.py'}
for name in ['bticino_ipc.py','bticino_audio_offer.py','bticino_incoming_dialog.py','bticino_homekit_call.py']:
    mapping[name] = base/name
for name in ['index.js','stream.js','incoming-call.js','two-way-audio.js']:
    mapping[name] = bridge/'plugin'/name
for name in mapping:
    source = stage/name
    if name.endswith('.py'):
        compile(source.read_bytes(), str(source), 'exec')
    else:
        subprocess.run(['/usr/local/bin/node','--check',str(source)],check=True)
config_file = base/'config.json'
bridge_file = bridge/'config.json'
config = json.loads(config_file.read_text())
bridge_config = json.loads(bridge_file.read_text())
accessories = [item for item in bridge_config.get('accessories',[]) if item.get('accessory') == 'BTicinoHOMETOUCH']
if len(accessories) != 1 or bridge_config['bridge']['port'] != 51991:
    raise SystemExit('Unexpected test bridge configuration')
config['incoming_audio'] = True
accessories[0]['enableTwoWayAudio'] = True
backup = Path(tempfile.mkdtemp(prefix='incoming-audio-',dir=str(base/'backups')))
targets = list(mapping.values()) + [config_file,bridge_file]
originals = {}
for index,target in enumerate(targets):
    if target.exists():
        saved = backup/str(index)
        shutil.copy2(str(target),str(saved))
        originals[target] = saved
    else:
        originals[target] = None
(backup/'manifest.json').write_text(json.dumps({str(k):str(v) if v else None for k,v in originals.items()},indent=2))
def replace(target, data, mode):
    fd,temp = tempfile.mkstemp(prefix='.audio-update-',dir=str(target.parent))
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data)
        os.chmod(temp,mode)
        os.replace(temp,str(target))
    finally:
        if os.path.exists(temp):os.unlink(temp)
try:
    for name,target in mapping.items():
        replace(target,(stage/name).read_bytes(),0o600 if name.endswith('.py') else 0o644)
    replace(config_file,(json.dumps(config,indent=2)+'\n').encode(),0o600)
    replace(bridge_file,(json.dumps(bridge_config,indent=2)+'\n').encode(),0o600)
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'],check=True)
except Exception:
    for target,saved in originals.items():
        if saved:shutil.copy2(str(saved),str(target))
        elif target.exists():target.unlink()
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'])
    raise
print('BACKUP='+str(backup))
print('INCOMING_AUDIO_INSTALLED; restart the test bridge')
