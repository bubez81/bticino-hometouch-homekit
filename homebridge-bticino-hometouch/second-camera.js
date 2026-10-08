'use strict';
// A second camera of the same entrance panel (a TVCC camera, which the Door
// Entry app reaches with its camera arrow). It is a plain Camera accessory in
// the Homebridge bridge, so it appears in Apple Home without a new pairing.
// Previews show the latest picture of a live view of that camera: requesting a
// call for every preview would tire the gateway.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {StreamManager} = require('./stream');

const PLUGIN = 'homebridge-bticino-hometouch';
const PLATFORM = 'BTicinoHometouch';
const DEFAULT_NAME = 'Esterno';

// The plant has a camera beyond the entrance panel's own (Tvcc, cid 10061).
function hasSecondCamera(storage) {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(storage, 'camera-candidates.json'), 'utf8'));
    return (data.candidates || []).some(candidate => String(candidate.cid) === '10061');
  } catch (_) {
    return false;
  }
}

function secondCameraName(config) {
  const name = typeof config.secondCameraName === 'string' ? config.secondCameraName.trim() : '';
  return name.slice(0, 64) || DEFAULT_NAME;
}

function readImage(file) {
  return fs.promises.readFile(file);
}

function mainSnapshot() {
  return new Promise((resolve, reject) => {
    const req = http.get('http://127.0.0.1:8766/snapshot.jpg', res => {
      const chunks = [];
      res.on('data', chunk => chunks.push(chunk));
      res.on('error', reject);
      res.on('end', () => res.statusCode === 200 ? resolve(Buffer.concat(chunks)) : reject(new Error('Snapshot unavailable')));
    });
    req.setTimeout(5000, () => req.destroy(new Error('Snapshot timeout')));
    req.on('error', reject);
  });
}

class SecondCamera {
  constructor({api, log, storage, config, socket, ffmpeg, cached, resize}) {
    Object.assign(this, {api, log, storage, config, socket, ffmpeg, cached, resize});
    this.name = secondCameraName(config);
    this.uuid = api.hap.uuid.generate('bticino-hometouch-camera-1');
    this.frame = path.join(storage, 'snapshots', 'cameras', 'camera-1.jpg');
  }

  // Called after Homebridge restored its cached accessories.
  setup() {
    const enabled = this.config.secondCamera !== false && hasSecondCamera(this.storage);
    const existing = this.cached.get(this.uuid);
    if (!enabled) {
      if (existing) this.api.unregisterPlatformAccessories(PLUGIN, PLATFORM, [existing]);
      return null;
    }
    const {hap} = this.api;
    const accessory = existing || new this.api.platformAccessory(this.name, this.uuid, hap.Categories.CAMERA);
    accessory.displayName = this.name;
    accessory.getService(hap.Service.AccessoryInformation)
      .setCharacteristic(hap.Characteristic.Manufacturer, 'BTicino')
      .setCharacteristic(hap.Characteristic.Model, 'HOMETOUCH Tvcc')
      .setCharacteristic(hap.Characteristic.SerialNumber, 'HOMETOUCH-TVCC-1');
    this.streamManager = new StreamManager(this.socket, this.log, {
      camera: 1, ffmpegPath: this.ffmpeg, audioFfmpegPath: this.ffmpeg, liveAudio: false,
    });
    accessory.configureController(new hap.CameraController({
      cameraStreamCount: 1,
      delegate: this.delegate(),
      streamingOptions: {
        proxy: false,
        supportedCryptoSuites: [0],
        video: {
          codec: {profiles: [0, 1, 2], levels: [0, 1, 2]},
          resolutions: [[1280, 720, 30], [640, 480, 30], [640, 360, 30], [320, 240, 30], [320, 240, 15], [320, 180, 30]],
        },
      },
    }));
    if (!existing) this.api.registerPlatformAccessories(PLUGIN, PLATFORM, [accessory]);
    this.api.on('shutdown', () => this.streamManager.closeAll().catch(() => {}));
    this.log.info(`BTicino HOMETOUCH second camera published: ${this.name}`);
    return accessory;
  }

  delegate() {
    return {
      prepareStream: (req, callback) => {
        this.log.info(`BTicino HOMETOUCH live requested for ${this.name}`);
        this.streamManager.prepare(req).then(response => callback(null, response), callback);
      },
      handleStreamRequest: (req, callback) => this.streamManager.handle(req)
        .then(() => callback(), err => { this.log.error(`HomeKit stream (${this.name}): ${err.message}`); callback(err); }),
      handleSnapshotRequest: async (req, callback) => {
        try {
          const image = await readImage(this.frame).catch(() => mainSnapshot());
          const {width, height} = req || {};
          let output = image;
          if (Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0) {
            output = await this.resize(this.ffmpeg, image, width, height).catch(() => image);
          }
          callback(null, output);
        } catch (err) {
          callback(err);
        }
      },
    };
  }
}

module.exports = {SecondCamera, hasSecondCamera, secondCameraName, DEFAULT_NAME};
