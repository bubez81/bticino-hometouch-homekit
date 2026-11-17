'use strict';
const assert = require('node:assert/strict');
const {addRingerControls} = require('./ringer-controls');

// Minimal HAP mock: a characteristic records its onGet/onSet and current value.
class Char {
  constructor() { this.handlers = {}; this.value = undefined; }
  onGet(fn) { this.handlers.get = fn; return this; }
  onSet(fn) { this.handlers.set = fn; return this; }
}
class Service {
  constructor(name, subtype) { this.name = name; this.subtype = subtype; this.chars = {}; this.values = {}; }
  char(key) { return (this.chars[key] ||= new Char()); }
  setCharacteristic(key, value) { this.values[key] = value; return this; }
  updateCharacteristic(key, value) { this.values[key] = value; this.char(key).value = value; return this; }
  getCharacteristic(key) { return this.char(key); }
  testCharacteristic(key) { return key in this.chars; }
  addOptionalCharacteristic(key) { this.char(key); }
  addCharacteristic(key) { this.char(key); return this.char(key); }
}
class SwitchService extends Service {}
class LightbulbService extends Service {}
const accessory = {services: [], addService(Type, name, subtype) { const s = new Type(name, subtype); this.services.push(s); return s; },
  getServiceById(Type, subtype) { return this.services.find(s => s instanceof Type && s.subtype === subtype); }};
const api = {hap: {Service: {Switch: SwitchService, Lightbulb: LightbulbService},
  Characteristic: {On: 'On', Name: 'Name', ConfiguredName: 'ConfiguredName', Brightness: 'Brightness'}}};
const log = {info() {}, warn() {}, error() {}};
const flush = () => new Promise(r => setTimeout(r, 5));

(async () => {
  const sent = [];
  let answer = {ok: true};
  const ipc = (_s, command, payload, timeout) => {
    assert.equal(command, 'ringer_command');
    assert.equal(timeout, 8000);
    sent.push(payload);
    return Promise.resolve(answer);
  };
  const services = addRingerControls(api, accessory, {mute: true, voicemail: true, volume: true}, '/tmp/sock', log, {ipc});
  assert.equal(services.length, 3);
  const mute = accessory.getServiceById(SwitchService, 'ringer-mute');
  const voicemail = accessory.getServiceById(SwitchService, 'ringer-voicemail');
  const volume = accessory.getServiceById(LightbulbService, 'ringer-volume');
  assert.ok(mute && voicemail && volume);

  // Mute ON -> command sent, optimistic state becomes true.
  await mute.char('On').handlers.set(true); await flush();
  assert.deepEqual(sent.at(-1), {control: 'mute', value: true});
  assert.equal(await mute.char('On').handlers.get(), true);

  // Voicemail ON.
  await voicemail.char('On').handlers.set(true); await flush();
  assert.deepEqual(sent.at(-1), {control: 'voicemail', value: true});

  // Failure reverts the switch to its last known state (still false here).
  answer = {ok: false, error: 'sip_unavailable'};
  await mute.char('On').handlers.set(false); await flush();   // false matches last state anyway
  answer = {ok: false, error: 'boom'};
  const before = await voicemail.char('On').handlers.get();   // true
  await voicemail.char('On').handlers.set(false); await flush();
  await flush();
  assert.equal(voicemail.values.On, before, 'failed set is reverted to last known state');
  assert.equal(await voicemail.char('On').handlers.get(), true);

  // Volume brightness sets a level 0..100.
  answer = {ok: true};
  await volume.char('Brightness').handlers.set(40); await flush();
  assert.deepEqual(sent.at(-1), {control: 'volume', value: 40});
  assert.equal(await volume.char('Brightness').handlers.get(), 40);

  // Volume omitted by default (volume !== true).
  accessory.services = [];
  const two = addRingerControls(api, accessory, {}, '/tmp/sock', log, {ipc});
  assert.equal(two.length, 2);
  assert.ok(!accessory.getServiceById(LightbulbService, 'ringer-volume'));

  console.log('RINGER_CONTROLS_OK (mock HAP only)');
})().catch(err => { console.error(err); process.exitCode = 1; });
