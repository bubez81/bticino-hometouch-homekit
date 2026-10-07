"""Read-only, redacted observations of the entire SIP receive path."""
import hashlib
import hmac
import re


def observe(raw, key, candidates=()):
    head, _, body = raw.decode('utf-8', errors='replace').partition('\r\n\r\n')
    rows = head.split('\r\n')
    headers = {}
    for row in rows[1:]:
        if ':' in row:
            name, value = row.split(':', 1)
            headers[name.lower()] = value.strip()
    cseq = headers.get('cseq', '').split()
    method = cseq[-1].upper() if cseq else rows[0].split(' ', 1)[0].upper()
    if method not in {'INVITE','UPDATE','ACK','MESSAGE','NOTIFY','INFO','BYE','CANCEL'}:
        return None
    def token(value):
        return hmac.new(key, value.strip().encode(), hashlib.sha256).hexdigest()[:12] if value.strip() else '-'
    status = re.match(r'^SIP/2.0 (\d{3})\b', rows[0])
    values = []
    scope = 'session'
    for line in body.replace('\r','').split('\n'):
        if line.startswith('m='):
            kind = line[2:].split(' ',1)[0]
            scope = kind if kind in ('audio','video','application') else 'other-media'
        match = re.match(r'^a=DEVADDR\s*[:=]\s*([^;\s]+)',line,re.I)
        if match:
            value = match[1]
            matches = [index+1 for index,item in enumerate(candidates)
                       if str(item.get('devaddr','')) == value]
            values.append({'scope':scope,'token':token(value),'candidate_indices':matches})
    return {'call':token(headers.get('call-id','')), 'method':method,
            'status':int(status[1]) if status else None,
            'cseq':int(cseq[0]) if cseq and cseq[0].isdigit() else None,
            'has_body':bool(body),'sdp':headers.get('content-type','').split(';')[0].lower()=='application/sdp',
            'devices':values}
