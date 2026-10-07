"""Incoming SIP dialog state machine. Network I/O is supplied by the listener."""
import secrets


def headers(raw):
    head=raw.split(b'\r\n\r\n',1)[0].decode('utf-8')
    rows={}
    for line in head.split('\r\n')[1:]:
        if ':' in line:
            key,value=line.split(':',1)
            rows.setdefault(key.lower(),[]).append(value.strip())
    return rows


class IncomingDialog:
    def __init__(self,invite,local_contact,send,to_tag=None):
        if not invite.startswith(b'INVITE '):raise ValueError('Expected INVITE')
        self.invite=invite
        self.headers=headers(invite)
        for name in ('via','from','to','call-id','cseq','contact'):
            if not self.headers.get(name):raise ValueError('Incomplete INVITE')
        self.contact=local_contact
        if '\r' in local_contact or '\n' in local_contact:raise ValueError('Invalid Contact')
        self.send=send
        self.tag=to_tag or secrets.token_hex(8)
        self.state='ringing'
        self.owner=None
        self.answer_packet=None
        self.local_cseq=0

    @property
    def call_id(self):return self.headers['call-id'][0]

    @property
    def local_identity(self):
        to=self.headers['to'][0]
        return to if ';tag=' in to.lower() else to+';tag='+self.tag

    def response(self,raw,code,reason,body=None):
        values=headers(raw)
        rows=[f'SIP/2.0 {code} {reason}']
        rows.extend('Via: '+value for value in values['via'])
        for name,pretty in [('from','From'),('to','To'),('call-id','Call-ID'),('cseq','CSeq')]:
            value=values[name][0]
            if name=='to' and ';tag=' not in value.lower():value+=';tag='+self.tag
            rows.append(f'{pretty}: {value}')
        if body is not None:
            rows.extend('Record-Route: '+value for value in self.headers.get('record-route',[]))
            rows.extend(['Contact: '+self.contact,'Content-Type: application/sdp'])
        body=body or ''
        return '\r\n'.join(rows+[f'Content-Length: {len(body.encode())}','',''])+body

    def answer(self,owner,sdp,audio_ready=False):
        if not isinstance(owner,str) or not owner:raise ValueError('Missing owner')
        if self.state in ('answered','established'):
            if owner!=self.owner:raise ValueError('Call already owned')
            return False
        if self.state!='ringing':raise ValueError('Call no longer ringing')
        if not audio_ready:raise ValueError('Audio transport is not ready')
        packet=self.response(self.invite,200,'OK',sdp)
        self.send(packet)
        self.answer_packet=packet
        self.owner=owner
        self.state='answered'
        return True

    def receive(self,raw):
        values=headers(raw)
        if values.get('call-id',[None])[0]!=self.call_id:return False
        first=raw.split(b'\r\n',1)[0].decode()
        method=first.split(' ',1)[0]
        expected_ack=self.headers['cseq'][0].split()[0]+' ACK'
        if method=='ACK' and self.state=='answered' and values.get('cseq')==[expected_ack]:self.state='established'
        elif method=='INVITE' and self.answer_packet and values.get('cseq')==self.headers.get('cseq'):
            self.send(self.answer_packet)
        elif method=='CANCEL':
            self.send(self.response(raw,200,'OK'))
            if self.state=='ringing':
                self.send(self.response(self.invite,487,'Request Terminated'))
                self.state='closed'
        elif method=='BYE':
            self.send(self.response(raw,200,'OK'));self.state='closed'
        elif first.startswith('SIP/2.0 200') and values.get('cseq',[''])[0]==f'{self.local_cseq} BYE':
            self.state='closed'
        return True

    def hangup(self,owner):
        if owner!=self.owner:raise ValueError('Call owner mismatch')
        if self.state in ('closing','closed'):return False
        if self.state not in ('answered','established'):raise ValueError('Call not answered')
        contact=self.headers['contact'][0]
        target=contact.split('<',1)[1].split('>',1)[0] if '<' in contact else contact.split(';',1)[0]
        if not target.startswith(('sip:','sips:')):raise ValueError('Invalid remote Contact')
        self.local_cseq+=1
        # UAS route set uses incoming Record-Route order (RFC 3261 12.1.1).
        local_uri=self.contact.strip('<>')
        host=local_uri.split('@',1)[-1].split(';',1)[0]
        rows=[f'BYE {target} SIP/2.0',f'Via: SIP/2.0/TLS {host};branch=z9hG4bK{secrets.token_hex(10)};rport',
              'Max-Forwards: 70','From: '+self.local_identity,'To: '+self.headers['from'][0],
              'Call-ID: '+self.call_id,f'CSeq: {self.local_cseq} BYE','Contact: '+self.contact]
        rows.extend('Route: '+value for value in self.headers.get('record-route',[]))
        self.send('\r\n'.join(rows+['Content-Length: 0','','']))
        self.state='closing'
        return True
