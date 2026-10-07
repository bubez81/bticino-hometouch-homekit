"""Opt-in, once-per-established-call contextual opening. No automatic retries."""
import secrets


def open_current_call(listener, owner, enabled, domain):
    if enabled is not True:
        return {'ok': False, 'error': 'opening_disabled'}
    if not listener.registered or len(listener.incoming_dialogs) != 1:
        return {'ok': False, 'error': 'no_unique_call'}
    call_id, dialog = next(iter(listener.incoming_dialogs.items()))
    capture = listener.media.get(call_id)
    attachment = getattr(capture, 'attachment', None)
    if (not owner or dialog.state != 'established' or dialog.owner != owner
            or attachment is None or attachment.owner != owner):
        return {'ok': False, 'error': 'answered_call_required'}
    if getattr(dialog, 'opening_attempted', False):
        return {'ok': False, 'error': 'opening_already_attempted'}
    # Validate all header inputs before constructing either message.
    for value in (domain, listener.username, listener.local_ip, str(listener.local_port)):
        if not value or any(ch.isspace() for ch in value) or any(ch in value for ch in '<>'):
            raise ValueError('Invalid SIP identity')
    packets = []
    for body in ('*8*19*4##', '*8*20*4##'):
        target = 'sip:MHT@' + domain
        rows = [f'MESSAGE {target} SIP/2.0',
                f'Via: SIP/2.0/TLS {listener.local_ip}:{listener.local_port};branch=z9hG4bK{secrets.token_hex(12)};rport',
                'Max-Forwards: 70',
                f'From: <sip:{listener.username}@{domain}>;tag={secrets.token_hex(12)}',
                f'To: <{target}>', f'Call-ID: {secrets.token_hex(16)}@{listener.local_ip}',
                f'CSeq: {listener.cseq} MESSAGE',
                f'Contact: <sip:{listener.username}@{listener.local_ip}:{listener.local_port};transport=tls>',
                'Content-Type: text/plain', f'Content-Length: {len(body)}', '', body]
        listener.cseq += 1
        packets.append('\r\n'.join(rows))
    # Mark before sending: an uncertain or partial send MUST NOT be retried.
    dialog.opening_attempted = True
    for packet in packets:
        listener.send(packet)
    return {'ok': True, 'state': 'sent_unconfirmed'}
