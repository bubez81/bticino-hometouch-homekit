#!/usr/bin/env python3
"""Install staged live code into the existing test bridge; preserve pairing."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import shlex
import signal
import time

stage = Path(__file__).resolve().parent
base = Path('/opt/bticino-sniffer')
child = Path('/private/tmp/bticino-child')
if os.geteuid() != 0:
    raise SystemExit('Run with sudo')
config = json.loads((child / 'config.json').read_text())
matches = [a for a in config.get('accessories', []) if a.get('accessory') == 'BTicinoHOMETOUCH']
if len(matches) != 1:
    raise SystemExit('Expected exactly one BTicino test accessory')
files = [(stage/'bticino_ipc.py',base/'bticino_ipc.py'),
         (stage/'probe-camera.py',base/'probe-camera.py'),
         (stage/'index.js',child/'plugin/index.js'),
         (stage/'stream.js',child/'plugin/stream.js')]
for source, target in files:
    if not source.is_file() or not target.is_file():
        raise SystemExit('Missing staged or installed file: '+str(target))
for source, _ in files[:2]:
    compile(source.read_text(),str(source),'exec')
node = '/usr/local/bin/node'
for source, _ in files[2:]:
    subprocess.run([node,'--check',str(source)],check=True)
backup = Path(tempfile.mkdtemp(prefix='live-backup-',dir=str(base/'backups')))
for _, target in files:
    shutil.copy2(target,backup/target.name)
shutil.copy2(child/'config.json',backup/'child-config.json')
print('BACKUP='+str(backup),flush=True)
try:
    for source,target in files:
        shutil.copyfile(source,target)
        os.chmod(target,0o700 if target.parent==base else 0o644)
    matches[0]['enableHapLive']=True
    matches[0]['enableCamera']=True
    matches[0]['ffmpegPath']='/opt/homebrew/opt/ffmpeg/bin/ffmpeg'
    (child/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'],check=True)
except Exception:
    for _,target in files:
        shutil.copy2(backup/target.name,target)
    shutil.copy2(backup/'child-config.json',child/'config.json')
    subprocess.run(['/bin/launchctl','kickstart','-k','system/io.github.bubez81.bticino-hometouch'])
    raise
rows = subprocess.check_output(['/bin/ps','-axo','pid=,ppid=,command='],text=True).splitlines()
parents = set()
processes = []
for row in rows:
    pid,ppid,command = row.strip().split(None,2)
    processes.append((int(pid),int(ppid),command))
    try:
        args=shlex.split(command)
        if '-U' in args and args[args.index('-U')+1] == str(child):
            parents.add(int(pid))
    except (ValueError,IndexError):
        pass
for pid,ppid,command in processes:
    if ppid in parents and command.strip() == 'homebridge':
        os.kill(pid,signal.SIGTERM)
        for attempt in range(50):
            try: os.kill(pid,0)
            except ProcessLookupError: break
            time.sleep(0.1)
        else: raise SystemExit('Test bridge did not stop; no second instance started')
print('LIVE_TEST_INSTALLED; starting test bridge',flush=True)
os.environ['PATH']='/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'
os.execv('/usr/local/bin/homebridge',['homebridge','-U',str(child),'-P',str(child/'plugin'),'--strict-plugin-resolution','-Q'])
