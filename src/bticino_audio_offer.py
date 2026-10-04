"""Validated audio negotiation for the two-way bridge; never log SDES keys."""
import base64
import ipaddress
import re


def parse_audio_offer(sdp):
    lines = sdp.replace('\r', '').split('\n')
    start = next((i for i, line in enumerate(lines) if line.startswith('m=audio ')), None)
    if start is None:
        return None
    first_media = next(i for i, line in enumerate(lines) if line.startswith('m='))
    end = next((i for i in range(start+1, len(lines)) if lines[i].startswith('m=')), len(lines))
    media, session = lines[start:end], lines[:first_media]
    fields = media[0].split()
    if len(fields)<4 or fields[2]!='RTP/SAVP':
        raise ValueError('Unsupported audio transport')
    port = int(fields[1])
    if port==0:
        return None
    if not 1024<=port<=65534:
        raise ValueError('Invalid audio port')
    connection = next((line for line in media if line.startswith('c=')), None)
    connection = connection or next((line for line in session if line.startswith('c=')), '')
    if not connection.startswith('c=IN IP4 '):
        raise ValueError('Unsupported audio address family')
    address = ipaddress.IPv4Address(connection[9:])
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local:
        raise ValueError('Invalid remote audio address')
    maps = {'0':('PCMU',8000,1),'8':('PCMA',8000,1)}
    for line in media:
        match = re.fullmatch(r'a=rtpmap:(\d+) ([A-Za-z0-9-]+)/(\d+)(?:/(\d+))?',line)
        if match:
            pt,codec,rate,channels=match.groups()
            maps[pt]=(codec.upper(),int(rate),int(channels or 1))
    chosen=None
    for pt in fields[3:]:
        codec,rate,channels=maps.get(pt,('',0,0))
        if (codec in ('PCMU','PCMA','SPEEX') and rate==8000 and channels==1) or (codec=='OPUS' and rate==48000 and channels in (1,2)):
            if not 0<=int(pt)<=127:
                raise ValueError('Invalid audio payload')
            chosen=(int(pt),codec,rate,channels)
            break
    if chosen is None:
        raise ValueError('No supported two-way audio codec')
    material=tag=None
    for line in media:
        match=re.fullmatch(r'a=crypto:(\d+) AES_CM_128_HMAC_SHA1_80 inline:([A-Za-z0-9+/=]+)',line)
        if match:
            tag=int(match[1]);material=base64.b64decode(match[2],validate=True)
            if len(material)!=30:
                raise ValueError('Invalid audio key length')
            break
    if material is None:
        raise ValueError('No supported audio encryption')
    rtcp_port=port+1
    rtcp_address=str(address)
    for line in media:
        if line.startswith('a=rtcp:'):
            parts=line[7:].split()
            rtcp_port=int(parts[0])
            if len(parts)>1:
                if len(parts)!=4 or parts[1:3]!=['IN','IP4'] or parts[3]!=str(address):
                    raise ValueError('Separate RTCP host is not supported')
    if not 1024<=rtcp_port<=65535:
        raise ValueError('Invalid audio RTCP port')
    if 'a=rtcp-mux' in media:
        rtcp_port=port
    direction=next((line[2:] for line in media if line in ('a=sendrecv','a=sendonly','a=recvonly','a=inactive')),None)
    direction=direction or next((line[2:] for line in session if line in ('a=sendrecv','a=sendonly','a=recvonly','a=inactive')),'sendrecv')
    pt,codec,rate,channels=chosen
    return dict(payload=pt,codec=codec,clock=rate,channels=channels,address=str(address),port=port,
                rtcp_address=rtcp_address,rtcp_port=rtcp_port,crypto_tag=tag,material=material,
                can_listen=direction in ('sendrecv','sendonly'),can_talk=direction in ('sendrecv','recvonly'))
