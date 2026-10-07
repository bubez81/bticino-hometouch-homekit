'use strict';
// Plugin log rotation, anonymisation and the diagnostics file, offline.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {PluginLog, teeLogger, redact} = require('./plugin-log');
const {buildDiagnostics} = require('./diagnostics');

(async () => {
  const storage = fs.mkdtempSync(path.join(os.tmpdir(), 'bticino-diag-test-'));

  // Rotation keeps three files and tail() returns the newest lines, oldest first.
  const file = new PluginLog(path.join(storage, 'plugin.log'), {maxBytes: 200, files: 3});
  for (let i = 0; i < 40; i++) file.write('info', `riga ${i}`);
  assert(fs.existsSync(path.join(storage, 'plugin.log.2')));
  assert(!fs.existsSync(path.join(storage, 'plugin.log.3')));
  const tail = file.tail(3);
  assert.deepEqual(tail.map(l => l.split(' ').pop()), ['37', '38', '39']);
  assert.equal(fs.statSync(path.join(storage, 'plugin.log')).mode & 0o777, 0o600);

  // The tee logger writes Homebridge and file, but not debug lines to the file.
  const seen = [];
  const tee = teeLogger({info: m => seen.push(m), warn: m => seen.push(m), error: m => seen.push(m), debug: m => seen.push(m)},
    new PluginLog(path.join(storage, 'tee.log')));
  tee.info('visibile'); tee.debug('solo homebridge');
  assert.deepEqual(seen, ['visibile', 'solo homebridge']);
  assert.match(fs.readFileSync(path.join(storage, 'tee.log'), 'utf8'), /INFO  visibile\n$/);

  // Anonymisation: stable placeholders, nothing identifying left.
  const text = redact('REGISTER sip:1234-ABCD@1727541.bs.iotleg.com da 192.168.1.2 verso 192.168.100.164, di nuovo 192.168.1.2; '
    + 'Domain: 1727541.bs.iotleg.com; mail a.b@example.com; MAC 0E:33:85:B7:03:EA; codice 489-28-086; '
    + 'a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=; token ' + 'x'.repeat(40) + '; locale 127.0.0.1; '
    + 'ffmpeg /Users/mario/.homebridge/node_modules/ffmpeg-for-homebridge/ffmpeg');
  for (const secret of ['1727541', '192.168.1.2', '192.168.100.164', 'a.b@example.com', '0E:33:85', '489-28-086', 'QUJDREVG', 'xxxxxxxxxx'])
    assert(!text.includes(secret), `left in: ${secret}`);
  assert.match(text, /da <ip-1> verso <ip-2>, di nuovo <ip-1>/);
  assert.match(text, /locale 127\.0\.0\.1/);
  assert.match(text, /ffmpeg \/Users\/<user>\/\.homebridge\/node_modules\/ffmpeg-for-homebridge\/ffmpeg/);

  // Diagnostics file: sections, checks that fail cleanly offline, logs, anonymised.
  fs.writeFileSync(path.join(storage, 'onboarding.json'), JSON.stringify({sip_server: '127.0.0.1', sip_port: 9,
    credentials_file: 'private/sip/sip_credentials.json'}));
  new PluginLog(path.join(storage, 'plugin.log')).write('warn', 'gateway 192.168.100.164 lento per user@example.com');
  fs.writeFileSync(path.join(storage, 'camera-calls.log'), '=== start_call\n[h264 @ 0x1] no frame!\nPROBE accepted=True\n');
  const report = await buildDiagnostics({storage, config: {name: 'Videocitofono', entrances: [{name: 'Scala', address: '20'}],
    ipcSocket: path.join(storage, 'missing.sock')}, versions: {plugin: '9.9.9'}, ffmpeg: null, python: 'python3'});
  for (const heading of ['## Versioni', '## Configurazione', '## Controlli', '## Log del plugin', '## Chiamate alla telecamera'])
    assert(report.includes(heading), heading);
  assert.match(report, /plugin 9\.9\.9/);
  assert.match(report, /credentials_file: MANCANTE/);
  assert.match(report, /ingressi: Scala=20/);
  assert.match(report, /listener \(IPC ping\): non disponibile/);
  assert.match(report, /PROBE accepted=True/);
  assert(!report.includes('no frame!'));
  assert(!report.includes('192.168.100.164') && !report.includes('user@example.com'));
  fs.rmSync(storage, {recursive: true, force: true});
  console.log('PLUGIN_LOG_REDACTION_DIAGNOSTICS_OK');
})().catch(error => { console.error(error); process.exit(1); });
