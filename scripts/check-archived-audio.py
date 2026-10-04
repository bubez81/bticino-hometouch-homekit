"""Read-only summary of historical audio offers; never prints SDES keys."""
import collections
from pathlib import Path
import re
import sys
sys.path.insert(0,'/opt/bticino-sniffer')
from bticino_audio_offer import parse_audio_offer
results=collections.Counter()
for path in sorted(Path('/opt/bticino-sniffer/logs').glob('*INVITE*.sip')):
    body=path.read_bytes().partition(b'\r\n\r\n')[2].decode(errors='replace')
    lines=body.replace('\r','').split('\n')
    start=next((i for i,v in enumerate(lines) if v.startswith('m=audio ')),None)
    if start is None:continue
    end=next((i for i in range(start+1,len(lines)) if lines[i].startswith('m=')),len(lines))
    audio=lines[start:end]
    try:
        parsed=parse_audio_offer(body)
        result='accepted '+str(parsed['codec']) if parsed else 'disabled'
    except ValueError as exc:
        result=str(exc) if str(exc) in ('No supported audio encryption','No supported two-way audio codec','Unsupported audio transport','Unsupported audio address family','Invalid remote audio address','Separate RTCP host is not supported') else type(exc).__name__
    crypto=[]
    for line in audio:
        match=re.match(r'a=crypto:(\d+) ([A-Z0-9_]+) inline:([^ ]+)(.*)',line)
        if match:crypto.append((match[2], 'has-lifetime-or-mki' if '|' in match[3] else 'plain-inline','has-session-params' if match[4].strip() else 'no-session-params'))
    codecs=re.findall(r'a=rtpmap:\d+ ([A-Za-z0-9/-]+)', '\n'.join(audio))
    results[(result,tuple(codecs),tuple(crypto))]+=1
for shape,count in results.items():print(count,shape)
