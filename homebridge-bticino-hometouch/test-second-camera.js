'use strict';
// Second camera (Tvcc) with mock HAP: published in the bridge only when the
// system has one, named from the settings, calls camera 1, previews from file.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const ipc = require('./ipc');
const calls = [];
ipc.request = async (_socket, command, payload) => { calls.push({command, ...payload}); return {ok: true}; };
const {SecondCamera, hasSecondCamera, secondCameraName} = require('./second-camera');

class AccessoryInformation { setCharacteristic() { return this; } }
class CameraController { constructor(options) { this.options = options; } }
class PlatformAccessory {
  constructor(name, uuid, category) { Object.assign(this, {displayName: name, UUID: uuid, category}); this.info = new AccessoryInformation(); this.controllers = []; }
  getService() { return this.info; }
  configureController(controller) { this.controllers.push(controller); }
}
const registered = [], unregistered = [];
const api = {
  on() {}, platformAccessory: PlatformAccessory,
  registerPlatformAccessories(_p, _n, list) { registered.push(...list); },
  unregisterPlatformAccessories(_p, _n, list) { unregistered.push(...list); },
  hap: {Service: {AccessoryInformation}, Characteristic: {Manufacturer: 'm', Model: 'mo', SerialNumber: 's'},
    CameraController, Categories: {CAMERA: 17}, uuid: {generate: value => `uuid:${value}`}},
};
const log = {info() {}, warn() {}, error() {}, debug() {}};
const storage = fs.mkdtempSync(path.join(os.tmpdir(), 'bticino-second-camera-'));
const candidates = list => fs.writeFileSync(path.join(storage, 'camera-candidates.json'), JSON.stringify({version: 1, candidates: list}));

(async () => {
  assert.equal(secondCameraName({}), 'Esterno');
  assert.equal(secondCameraName({secondCameraName: '  Cancello  '}), 'Cancello');

  // No Tvcc: nothing published.
  candidates([{cid: '10050', devaddr: '20'}]);
  assert.equal(hasSecondCamera(storage), false);
  const none = new SecondCamera({api, log, storage, config: {}, socket: '/tmp/x.sock', ffmpeg: 'ffmpeg', cached: new Map(), resize: async (_ffmpeg, image) => image});
  assert.equal(none.setup(), null);
  assert.equal(registered.length, 0);

  // Tvcc present: one Camera accessory in the bridge, with the chosen name.
  candidates([{cid: '10050', devaddr: '20'}, {cid: '10061', devaddr: '20'}]);
  const camera = new SecondCamera({api, log, storage, config: {secondCameraName: 'Cancello'}, socket: '/tmp/x.sock', ffmpeg: 'ffmpeg', cached: new Map(), resize: async (_ffmpeg, image) => image});
  const accessory = camera.setup();
  assert.equal(accessory.displayName, 'Cancello');
  assert.equal(accessory.category, 17);
  assert.equal(registered.length, 1);
  assert.equal(accessory.controllers.length, 1);
  assert.equal(camera.streamManager.config.camera, 1);

  // Previews come from the latest picture of that camera, never from a new call.
  fs.mkdirSync(path.dirname(camera.frame), {recursive: true});
  fs.writeFileSync(camera.frame, Buffer.from('jpeg-of-camera-1'));
  const snapshot = await new Promise((resolve, reject) => accessory.controllers[0].options.delegate
    .handleSnapshotRequest({width: 640, height: 360}, (err, image) => err ? reject(err) : resolve(image)));
  assert.equal(snapshot.toString(), 'jpeg-of-camera-1');
  assert.equal(calls.filter(c => c.command === 'start_call').length, 0);

  // Restored from cache: reused, not registered twice; disabled: removed.
  const cached = new Map([[accessory.UUID, accessory]]);
  new SecondCamera({api, log, storage, config: {}, socket: '/tmp/x.sock', ffmpeg: 'ffmpeg', cached, resize: async (_ffmpeg, image) => image}).setup();
  assert.equal(registered.length, 1);
  new SecondCamera({api, log, storage, config: {secondCamera: false}, socket: '/tmp/x.sock', ffmpeg: 'ffmpeg', cached, resize: async (_ffmpeg, image) => image}).setup();
  assert.deepEqual(unregistered, [accessory]);

  fs.rmSync(storage, {recursive: true, force: true});
  console.log('SECOND_CAMERA_OK (mock HAP only)');
})().catch(error => { console.error(error); process.exit(1); });
