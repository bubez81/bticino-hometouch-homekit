"""Entrance opening: a press/release pulse sent as SIP MESSAGE to the gateway.

The gateway accepts `*8*19*<address>##` (press) and `*8*20*<address>##`
(release) for the lock actuator at <address>. A 200 response confirms delivery
to the gateway, not that the door physically opened.

The opener outlives individual SIP connections, so a release whose send fails
during a reconnect is retried on the next connection until a deadline.
"""
import re
import secrets
import time

NAME = re.compile(r'^[a-z0-9_-]{1,32}$')
ADDRESS = re.compile(r'^[0-9]{1,4}$')
RELEASE_RETRY_SECONDS = 15.0
TRANSACTION_TTL_SECONDS = 30.0


def build_message(sender, domain, body):
    for value in (domain, sender.username, sender.local_ip, str(sender.local_port)):
        if not value or any(ch.isspace() for ch in value) or any(ch in value for ch in '<>'):
            raise ValueError('Invalid SIP identity')
    target = 'sip:MHT@' + domain
    call_id = f'{secrets.token_hex(16)}@{sender.local_ip}'
    rows = [f'MESSAGE {target} SIP/2.0',
            f'Via: SIP/2.0/TLS {sender.local_ip}:{sender.local_port};branch=z9hG4bK{secrets.token_hex(12)};rport',
            'Max-Forwards: 70',
            f'From: <sip:{sender.username}@{domain}>;tag={secrets.token_hex(12)}',
            f'To: <{target}>', f'Call-ID: {call_id}',
            f'CSeq: {sender.cseq} MESSAGE',
            f'Contact: <sip:{sender.username}@{sender.local_ip}:{sender.local_port};transport=tls>',
            'Content-Type: text/plain', f'Content-Length: {len(body)}', '', body]
    sender.cseq += 1
    return call_id, '\r\n'.join(rows)


class EntranceOpener:
    def __init__(self, entrances, pulse_seconds=1.0, enabled=False, clock=time.monotonic, log=print):
        self.entrances = {}
        for name, address in (entrances or {}).items():
            name, address = str(name), str(address)
            if not NAME.match(name) or not ADDRESS.match(address):
                raise ValueError(f'Invalid entrance {name!r}: {address!r}')
            self.entrances[name] = address
        self.pulse_seconds = float(pulse_seconds)
        if not 0.2 <= self.pulse_seconds <= 10:
            raise ValueError('entrance pulse must be between 0.2 and 10 seconds')
        self.enabled = enabled is True
        self.clock = clock
        self.log = log
        self.active = None
        self.transactions = {}
        self.results = {}

    @classmethod
    def from_config(cls, config, log=print):
        return cls(config.get('entrances', {}), config.get('entrance_pulse_seconds', 1.0),
                   config.get('entrance_open_enabled', False), log=log)

    def request(self, name, sender, domain):
        if not self.enabled:
            return {'ok': False, 'error': 'opening_disabled'}
        if name not in self.entrances:
            return {'ok': False, 'error': 'unknown_entrance'}
        if self.active is not None:
            return {'ok': False, 'error': 'entrance_busy'}
        address = self.entrances[name]
        now = self.clock()
        self.active = {'name': name, 'address': address, 'release_at': now + self.pulse_seconds,
                       'give_up_at': now + self.pulse_seconds + RELEASE_RETRY_SECONDS}
        self.results[name] = {'requested': time.time(), 'press': 'sending', 'release': 'pending'}
        self.log(f'APERTURA {name} ({address}): pressione')
        try:
            self._send(sender, domain, name, 'press', f'*8*19*{address}##')
        except (OSError, ValueError) as error:
            # The press may or may not have left: always attempt the release.
            self.results[name]['press'] = f'send_failed: {error}'
            self.log(f'APERTURA {name}: invio pressione fallito: {error}')
        return {'ok': True, 'state': 'sent_unconfirmed', 'entrance': name}

    def tick(self, sender, domain):
        now = self.clock()
        for call_id, txn in list(self.transactions.items()):
            if now - txn['sent'] > TRANSACTION_TTL_SECONDS:
                del self.transactions[call_id]
        active = self.active
        if active is None or now < active['release_at']:
            return
        name = active['name']
        try:
            if sender is None:
                raise OSError('SIP non connesso')
            self._send(sender, domain, name, 'release', f'*8*20*{active["address"]}##')
            self.log(f'APERTURA {name} ({active["address"]}): rilascio')
            self.active = None
        except (OSError, ValueError) as error:
            if now >= active['give_up_at']:
                self.results[name]['release'] = f'send_failed: {error}'
                self.log(f'APERTURA {name}: rilascio non inviato: {error}')
                self.active = None

    def on_response(self, call_id, status_line):
        txn = self.transactions.get(call_id)
        if txn is None:
            return False
        code = status_line.split(' ', 2)[1] if status_line.count(' ') >= 1 else ''
        elapsed = int((self.clock() - txn['sent']) * 1000)
        if not code.startswith('1'):
            del self.transactions[call_id]
            self.results[txn['name']][txn['phase']] = status_line
        self.log(f'APERTURA {txn["name"]}: {txn["phase"]} -> {status_line} in {elapsed} ms')
        return True

    def status(self):
        return {'ok': True, 'enabled': self.enabled, 'pulse_seconds': self.pulse_seconds,
                'entrances': sorted(self.entrances), 'busy': self.active is not None,
                'results': {name: dict(result) for name, result in self.results.items()}}

    def _send(self, sender, domain, name, phase, body):
        call_id, packet = build_message(sender, domain, body)
        self.transactions[call_id] = {'name': name, 'phase': phase, 'sent': self.clock()}
        self.results[name][phase] = 'sent'
        sender.send(packet)
