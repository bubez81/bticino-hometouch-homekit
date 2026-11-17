"""Ringer and answering-machine controls, sent as OpenWebNet frames in a SIP MESSAGE.

The gateway accepts an OpenWebNet command carried in a SIP MESSAGE body and answers
200 (delivery confirmed), exactly like the door opener in `bticino_entrance_open`.
It never returns the stored value, though: a dimension *read* over this path gets a
200 but no reply (verified on the HOMETOUCH gateway). So these controls are
write-only / optimistic — they remember the last value we set, which is unknown
until the first command and can drift if the setting is changed on the panel itself.

Frames (WHO=8, door entry):
  ringer mute   set silent   *#8**#33*0##     set audible   *#8**#33*1##
  answering m.  enable       *8*91##          disable       *8*92##
  ringer volume set level     *#8**#41*<code>##   (code mapping is install-specific)
"""
import time

from bticino_entrance_open import build_message

TRANSACTION_TTL_SECONDS = 30.0

MUTE_SILENT = '*#8**#33*0##'
MUTE_AUDIBLE = '*#8**#33*1##'
VOICEMAIL_ON = '*8*91##'
VOICEMAIL_OFF = '*8*92##'

# Experimental default: the OpenWebNet volume "code" is reportedly step * level, but
# the step is install-specific and there is no read-back to calibrate against, so the
# mapping below (a plain 0..7 ladder) is a starting point to tune live.
DEFAULT_VOLUME_CODES = [0, 1, 2, 3, 4, 5, 6, 7]

CONTROLS = ('mute', 'voicemail', 'volume')


def volume_frame(code):
    return f'*#8**#41*{int(code)}##'


class RingerControl:
    """Optimistic ringer / answering-machine / volume controls over SIP MESSAGE."""

    def __init__(self, enabled=False, volume_enabled=False, volume_codes=None,
                 clock=time.monotonic, log=print):
        self.enabled = enabled is True
        self.volume_enabled = volume_enabled is True
        codes = volume_codes if volume_codes else DEFAULT_VOLUME_CODES
        if (not isinstance(codes, (list, tuple)) or not codes
                or not all(isinstance(c, int) and 0 <= c <= 999 for c in codes)):
            raise ValueError('ringer_volume_codes must be a non-empty list of integers 0..999')
        self.volume_codes = list(codes)
        self.clock = clock
        self.log = log
        # Optimistic last-set state; None means "never set / unknown".
        self.state = {'mute': None, 'voicemail': None, 'volume': None}
        self.transactions = {}
        self.results = {}

    @classmethod
    def from_config(cls, config, log=print):
        return cls(enabled=config.get('ringer_enabled', False),
                   volume_enabled=config.get('ringer_volume_enabled', False),
                   volume_codes=config.get('ringer_volume_codes'),
                   log=log)

    def _body_for(self, control, value):
        """Return (own_body, normalized_value) or raise ValueError."""
        if control == 'mute':
            if not isinstance(value, bool):
                raise ValueError('mute value must be a boolean')
            return (MUTE_SILENT if value else MUTE_AUDIBLE), value
        if control == 'voicemail':
            if not isinstance(value, bool):
                raise ValueError('voicemail value must be a boolean')
            return (VOICEMAIL_ON if value else VOICEMAIL_OFF), value
        if control == 'volume':
            if not self.volume_enabled:
                raise ValueError('volume control disabled')
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise ValueError('volume value must be an integer 0..100')
            index = 0 if len(self.volume_codes) == 1 else round(value / 100 * (len(self.volume_codes) - 1))
            return volume_frame(self.volume_codes[index]), value
        raise ValueError(f'unknown control {control!r}')

    def request(self, request, sender, domain):
        if not self.enabled:
            return {'ok': False, 'error': 'ringer_disabled'}
        control = request.get('control')
        if control not in CONTROLS:
            return {'ok': False, 'error': 'unknown_control'}
        try:
            body, value = self._body_for(control, request.get('value'))
        except ValueError as error:
            return {'ok': False, 'error': 'invalid_value', 'detail': str(error)}
        self.results[control] = {'requested': time.time(), 'send': 'sending'}
        self.log(f'SUONERIA {control}={value}: invio')
        try:
            self._send(sender, domain, control, body)
        except (OSError, ValueError) as error:
            self.results[control]['send'] = f'send_failed: {error}'
            self.log(f'SUONERIA {control}: invio fallito: {error}')
            return {'ok': False, 'error': 'send_failed', 'control': control}
        # Optimistic: assume the command took effect (no read-back exists).
        self.state[control] = value
        return {'ok': True, 'state': 'sent', 'control': control, 'value': value}

    def tick(self):
        now = self.clock()
        for call_id, txn in list(self.transactions.items()):
            if now - txn['sent'] > TRANSACTION_TTL_SECONDS:
                del self.transactions[call_id]

    def on_response(self, call_id, status_line):
        txn = self.transactions.get(call_id)
        if txn is None:
            return False
        code = status_line.split(' ', 2)[1] if status_line.count(' ') >= 1 else ''
        elapsed = int((self.clock() - txn['sent']) * 1000)
        if not code.startswith('1'):
            del self.transactions[call_id]
            self.results[txn['control']]['send'] = status_line
        self.log(f'SUONERIA {txn["control"]}: {status_line} in {elapsed} ms')
        return True

    def status(self):
        return {'ok': True, 'enabled': self.enabled, 'volume_enabled': self.volume_enabled,
                'state': dict(self.state),
                'results': {name: dict(result) for name, result in self.results.items()}}

    def _send(self, sender, domain, control, body):
        call_id, packet = build_message(sender, domain, body)
        self.transactions[call_id] = {'control': control, 'sent': self.clock()}
        sender.send(packet)
