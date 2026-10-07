'use strict';
// All-in-one platform with mock HAP and a fake "python" that records how the
// listener would be started. No network, no real listener.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ipc = require('./ipc');
const calls = [];
ipc.request = async (_socket, command, payload) => { calls.push({command, ...payload}); return command === 'open_entrance' ? {ok: true} : {ok: true}; };

class Service {
  constructor(name, subtype) { this.displayName = name; this.subtype = subtype; this.chars = new Map(); this.values = {}; }
  getCharacteristic(key) {
    if (!this.chars.has(key)) this.chars.set(key, {subscriptions: 0, onGet() { return this; },
      onSet: fn => { this.handlers = {...this.handlers, [key]: fn}; return this.chars.get(key); }, sendEventNotification() {}});
    return this.chars.get(key);
  }
  setCharacteristic(key, value) { this.values[key] = value; return this; }
  updateCharacteristic(key, value) { this.values[key] = value; return this; }
  testCharacteristic() { return false; }
  addOptionalCharacteristic() {}
  setPrimaryService() {}
}
class Doorbell extends Service {} class AccessoryInformation extends Service {} class LockMechanism extends Service {}
class DoorbellController { constructor(options) { this.options = options; } on() {} setSpeakerMuted() {} }
class PlatformAccessory {
  constructor(name, uuid, category) { Object.assign(this, {displayName: name, UUID: uuid, category}); this.services = [new AccessoryInformation()]; this.controllers = []; }
  getService(type) { return this.services.find(s => s instanceof type); }
  getServiceById(type, subtype) { return this.services.find(s => s instanceof type && s.subtype === subtype); }
  addService(service, name, subtype) { const s = typeof service === 'function' ? new service(name, subtype) : service; this.services.push(s); return s; }
  configureController(controller) { this.controllers.push(controller); }
}
const Characteristic = {Manufacturer: 'm', Model: 'mo', SerialNumber: 's', Name: 'name', ProgrammableSwitchEvent: {SINGLE_PRESS: 0},
  LockCurrentState: {SECURED: 1, UNSECURED: 0}, LockTargetState: 'target'};
const platforms = {}, handlers = {}, published = [];
const storageRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'bticino-platform-test-'));
const api = {
  registerPlatform(_p, name, ctor) { platforms[name] = ctor; }, registerAccessory() {},
  on(event, fn) { (handlers[event] ||= []).push(fn); },
  platformAccessory: PlatformAccessory,
  publishExternalAccessories(plugin, accessories) { published.push({plugin, accessories}); },
  user: {storagePath: () => storageRoot},
  hap: {Service: {Doorbell, AccessoryInformation, LockMechanism}, Characteristic, DoorbellController,
    Categories: {VIDEO_DOORBELL: 18}, uuid: {generate: value => `uuid:${value}`}},
};
const lines = [];
const log = {info: m => lines.push(m), warn: m => lines.push(m), error: m => lines.push(m), debug() {}};

// Fake interpreter: records its arguments and environment, then waits for SIGTERM.
const record = path.join(storageRoot, 'record.json');
const fake = path.join(storageRoot, 'fake-python');
fs.writeFileSync(fake, `#!${process.execPath}\nrequire('fs').writeFileSync(${JSON.stringify(record)}, JSON.stringify({args: process.argv.slice(2), env: process.env}));\nprocess.on('SIGTERM', () => process.exit(0));\nconsole.log('REGISTRAZIONE SIP OK');\nsetInterval(() => {}, 1000);\n`, {mode: 0o700});

require('./index')(api);
(async () => {
  // Not set up: nothing published, a clear hint in the log.
  const empty = new platforms.BTicinoHometouch(log, {name: 'Videocitofono'}, api);
  assert.equal(empty.doorbell, undefined);
  assert(lines.some(l => /not set up yet/.test(l)));

  const storage = path.join(storageRoot, 'bticino-hometouch');
  fs.mkdirSync(storage, {recursive: true});
  fs.writeFileSync(path.join(storage, 'onboarding.json'), JSON.stringify({sip_server: '192.0.2.10', sip_domain: 'example.invalid',
    credentials_file: 'private/sip/credentials.json'}));
  const platform = new platforms.BTicinoHometouch(log, {name: 'Videocitofono', pythonPath: fake, ffmpegPath: '/opt/ffmpeg',
    entrances: [{name: 'Scala', address: '20'}, {name: 'Cancello Esterno', address: 21}, {name: 'bad', address: 'x'}],
    homeAssistant: {enabled: true, port: 8790}, ipcSocket: path.join(storageRoot, 'shared.sock')}, api);

  const config = JSON.parse(fs.readFileSync(path.join(storage, 'listener.json'), 'utf8'));
  assert.equal(fs.statSync(path.join(storage, 'listener.json')).mode & 0o777, 0o600);
  assert.equal(config.base_dir, storage);
  assert.equal(config.sip_server, '192.0.2.10');
  assert.equal(config.credentials_file, path.join(storage, 'private/sip/credentials.json'));
  assert.equal(config.ffmpeg, '/opt/ffmpeg');
  assert.equal(config.audio_ffmpeg, '/opt/ffmpeg');
  assert.equal(config.incoming_audio, true);
  assert.deepEqual(config.entrances, {scala: '20', cancello_esterno: '21'});
  assert.equal(config.entrance_open_enabled, true);
  assert.equal(config.api.port, 8790);
  assert.equal(fs.statSync(config.api.token_file).mode & 0o777, 0o600);

  for (let i = 0; i < 50 && !fs.existsSync(record); i++) await new Promise(r => setTimeout(r, 50));
  const started = JSON.parse(fs.readFileSync(record, 'utf8'));
  assert(started.args[0].endsWith('bticino_hometouch_listener.py'));
  assert.equal(started.env.BTICINO_SNIFFER_CONFIG, path.join(storage, 'listener.json'));
  assert.equal(started.env.BTICINO_IPC_SOCKET, path.join(storageRoot, 'shared.sock'));
  assert.equal(started.env.BTICINO_IPC_ENABLE_CALLS, '1');
  assert(started.env.BTICINO_CAMERA_PROBE.endsWith('probe-camera.py'));
  assert(fs.existsSync(started.env.BTICINO_LISTENER));
  await new Promise(r => setTimeout(r, 200));
  assert(lines.some(l => l === '[listener] REGISTRAZIONE SIP OK'));

  // One Video Doorbell accessory with a lock per valid entrance.
  handlers.didFinishLaunching.forEach(fn => fn());
  const accessory = published.at(-1).accessories[0];
  assert.equal(accessory.category, 18);
  const locks = accessory.services.filter(s => s instanceof LockMechanism);
  assert.deepEqual(locks.map(s => [s.displayName, s.subtype]), [['Scala', 'scala'], ['Cancello Esterno', 'cancello_esterno']]);
  assert.equal(locks[0].values.target, Characteristic.LockCurrentState.SECURED);

  // Opening: the right entrance through IPC, shown unlocked, then locked again.
  await locks[1].handlers.target(Characteristic.LockCurrentState.UNSECURED);
  assert.deepEqual(calls.filter(c => c.command === 'open_entrance'), [{command: 'open_entrance', entrance: 'cancello_esterno'}]);
  assert.equal(locks[1].values[Characteristic.LockCurrentState], Characteristic.LockCurrentState.UNSECURED);

  // Shutdown stops the listener cleanly.
  await platform.supervisor.stop();
  assert.equal(platform.supervisor.child, null);
  console.log('PLATFORM_LISTENER_DOORBELL_LOCKS_OK (mock HAP, fake listener)');
  fs.rmSync(storageRoot, {recursive: true, force: true});
  process.exit(0);
})().catch(error => { console.error(error); process.exit(1); });
