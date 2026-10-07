'use strict';

const { request } = require('./ipc');
const http = require('node:http');
const { spawn } = require('node:child_process');
const { StreamManager } = require('./stream');
const { installCallUnlock } = require('./call-unlock');
const path = require('node:path');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { ListenerSupervisor } = require('./supervisor');
const { addEntranceLocks, normalizeEntrances } = require('./entrance-locks');
const managers = new WeakMap();
const cameraKey = config => JSON.stringify([config.ipcSocket || '/tmp/bticino-hometouch.sock', config.name || 'BTicino HOMETOUCH']);

/**
 * Bridged BTicino HOMETOUCH accessory.
 * The accessory owns the doorbell and camera services; SIP/RTP remains in the
 * existing listener and is reached through the local IPC socket.
 */
module.exports = (api) => {
  managers.set(api,new Map());
  api.registerPlatform('homebridge-bticino-hometouch', 'BTicinoHometouch', BTicinoPlatform);
  api.registerAccessory('homebridge-bticino-hometouch', 'BTicinoHOMETOUCH', BTicinoAccessory);
  api.registerAccessory('homebridge-bticino-hometouch', 'BTicinoCallLock', BTicinoCallLock);
};

// FFmpeg for video, snapshots and audio: ffmpeg-for-homebridge includes libspeex
// (the gateway's audio codec) and libopus (HomeKit's).
function bundledFfmpeg() {
  try { return require('ffmpeg-for-homebridge'); } catch (_) { return null; }
}

/**
 * All-in-one platform: runs the bundled listener and publishes one Video
 * Doorbell accessory with live view, two-way audio and one lock per entrance.
 */
class BTicinoPlatform {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    this.api = api;
    const storage = this.config.storagePath || path.join(api.user.storagePath(), 'bticino-hometouch');
    const ffmpeg = this.config.ffmpegPath || bundledFfmpeg() || 'ffmpeg';
    const entrances = normalizeEntrances(this.config.entrances);
    // The settings page creates storage/python (with pyzipper); otherwise the system python3.
    const venv = path.join(storage, 'python', 'bin', 'python3');
    const python = this.config.pythonPath || (fs.existsSync(venv) ? venv : 'python3');
    this.supervisor = new ListenerSupervisor({storage, python, ffmpeg, socket: this.config.ipcSocket,
      settings: {entrances, api: this.homeAssistantApi(storage)}, log});
    if (!this.supervisor.start()) return;
    this.doorbell = new BTicinoAccessory(log, {
      name: this.config.name || 'Videocitofono', ipcSocket: this.supervisor.socket,
      enableCamera: true, enableHapLive: true, enableTwoWayAudio: true, standalone: true,
      ffmpegPath: ffmpeg, audioFfmpegPath: ffmpeg, liveAudio: this.config.liveAudio !== false,
      entrances, serialNumber: this.config.serialNumber,
    }, api);
    api.on('shutdown', () => this.supervisor.stop());
  }

  // Optional network API for Home Assistant: token created once, shown in the log.
  homeAssistantApi(storage) {
    const ha = this.config.homeAssistant;
    if (!ha || ha.enabled !== true) return null;
    const file = path.join(storage, 'private', 'api_token');
    if (!fs.existsSync(file)) {
      fs.mkdirSync(path.dirname(file), {recursive: true, mode: 0o700});
      fs.writeFileSync(file, crypto.randomBytes(32).toString('base64url') + '\n', {mode: 0o600});
      this.log.info(`Home Assistant: token created in ${file}; enter it in the BTicino HOMETOUCH integration`);
    }
    const port = Number.isInteger(ha.port) && ha.port >= 1024 && ha.port <= 65535 ? ha.port : 8790;
    return {enabled: true, bind: '0.0.0.0', port, token_file: file,
      allowed_clients: Array.isArray(ha.allowedClients) ? ha.allowedClients : [],
      ...(ha.liveRtspUrl ? {live_rtsp_url: ha.liveRtspUrl} : {})};
  }

  configureAccessory() {}
}

class BTicinoCallLock {
  constructor(log,config,api) {
    const key=cameraKey({...config,name:config.cameraName});
    const manager={get sessions(){
      const entry=managers.get(api)?.get(key);
      if(!entry)throw Error('Videocitofono associato non disponibile');
      return entry.sessions;
    }};
    this.service=installCallUnlock(api,manager,config,log);
    this.service.setPrimaryService();
    log.info('Apri ingresso: serratura separata; assegnare alla stanza del videocitofono in Casa');
  }
  getServices(){return [this.service];}
}

// Scale and letterbox a JPEG to the size HomeKit asked for, as camera-ffmpeg and unifi-protect do.
function resizeSnapshot(ffmpegPath, image, width, height) {
  return new Promise((resolve, reject) => {
    const filter = `scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2`;
    const ffmpeg = spawn(ffmpegPath, ['-hide_banner', '-loglevel', 'error', '-f', 'image2pipe', '-i', 'pipe:0',
      '-vf', filter, '-frames:v', '1', '-f', 'image2', '-c:v', 'mjpeg', '-q:v', '3', 'pipe:1']);
    const chunks = []; let stderr = '';
    const timer = setTimeout(() => { ffmpeg.kill('SIGKILL'); reject(new Error('resize timeout')); }, 4000);
    ffmpeg.stdout.on('data', chunk => chunks.push(chunk));
    ffmpeg.stderr.on('data', chunk => { stderr += chunk; });
    ffmpeg.on('error', err => { clearTimeout(timer); reject(err); });
    ffmpeg.on('close', code => {
      clearTimeout(timer);
      const out = Buffer.concat(chunks);
      code === 0 && out.length > 0 ? resolve(out) : reject(new Error(`ffmpeg exit ${code} ${stderr.trim()}`));
    });
    ffmpeg.stdin.on('error', () => {});
    ffmpeg.stdin.end(image);
  });
}

class BTicinoAccessory {
  constructor(log, config, api) {
    this.log = log;
    this.config = config || {};
    if (process.env.BTICINO_CAMERA_ONLY === '1') this.config = {...this.config, cameraOnly:true};
    this.api = api;
    this.streamManager = new StreamManager(this.config.ipcSocket || '/tmp/bticino-hometouch.sock', log, this.config);
    this.log.info('BTicino HOMETOUCH bridged accessory loaded');
    const name = this.config.name || 'BTicino HOMETOUCH';
    this.doorbellService = new this.api.hap.Service.Doorbell(name);
    if(this.config.enableCallUnlock===true && this.config.separateCallUnlock===true) {
      const entries=managers.get(api),key=cameraKey(this.config);
      if(entries.has(key))throw Error('Associazione videocitofono duplicata');
      entries.set(key,this.streamManager);
      api.on('shutdown',()=>entries.delete(key));
    } else if(this.config.enableCallUnlock===true) {
      this.unlockService=installCallUnlock(api,this.streamManager,this.config,log);
    }
    this.controllers = [];
    if (this.config.enableCamera === true) this.publishCamera(name);
    // standalone: publish the doorbell as its own HAP accessory with the Video Doorbell
    // category (18), like a native HomeKit video doorbell, instead of inside the bridge.
    if (this.config.standalone === true) this.publishStandalone(name);
    this.api.on('shutdown', () => { if (this.timer) clearInterval(this.timer); clearTimeout(this.retry); this.streamManager.closeAll().catch(err => this.log.error(err.message)); });
    this.didFinishLaunching();
  }

  getServices() {
    if (this.externalAccessory) return this.unlockService ? [this.unlockService] : [];
    return [...(this.config.cameraOnly ? [] : [this.doorbellService]),...(this.unlockService ? [this.unlockService] : [])];
  }
  getControllers() { return this.externalAccessory ? [] : this.controllers; }

  publishStandalone(name) {
    const {hap} = this.api;
    const accessory = new this.api.platformAccessory(name, hap.uuid.generate(`bticino-hometouch-standalone:${name}`), hap.Categories.VIDEO_DOORBELL);
    accessory.getService(hap.Service.AccessoryInformation)
      .setCharacteristic(hap.Characteristic.Manufacturer, 'BTicino')
      .setCharacteristic(hap.Characteristic.Model, 'HOMETOUCH')
      .setCharacteristic(hap.Characteristic.SerialNumber, this.config.serialNumber || 'HOMETOUCH');
    if (!this.config.cameraOnly) accessory.addService(this.doorbellService);
    this.controllers.forEach(controller => accessory.configureController(controller));
    if (Array.isArray(this.config.entrances) && this.config.entrances.length)
      this.lockServices = addEntranceLocks(this.api, accessory, this.config.entrances,
        this.config.ipcSocket || '/tmp/bticino-hometouch.sock', this.log);
    this.externalAccessory = accessory;
    this.api.on('didFinishLaunching', () => {
      this.api.publishExternalAccessories('homebridge-bticino-hometouch', [accessory]);
      this.log.info(`BTicino HOMETOUCH pubblicato come accessorio indipendente (Video Doorbell): ${name}`);
    });
  }

  didFinishLaunching(attempt = 0) {
    const socket = this.config.ipcSocket || '/tmp/bticino-hometouch.sock';
    request(socket, 'ping').then(() => {
      this.log.info('BTicino HOMETOUCH IPC connected');
      this.poll(socket);
    }).catch(() => {
      // The listener may still be starting: keep trying instead of giving up.
      if (attempt === 0) this.log.warn('BTicino HOMETOUCH IPC unavailable; waiting for the listener');
      this.retry = setTimeout(() => this.didFinishLaunching(attempt + 1), 5000);
      this.retry.unref?.();
    });
  }

  publishCamera(name) {
    if (!this.api.hap.DoorbellController) {
      this.log.warn('DoorbellController non disponibile in questa versione di HAP');
      return;
    }
    const delegate = {
      prepareStream: (_request, callback) => {
        this.log.warn('HomeKit richiede il live, ma il live è disabilitato nella configurazione del plugin');
        callback(new Error('Live streaming disabled'));
      },
      handleStreamRequest: (_request, callback) => {
        callback(new Error('Live streaming disabled'));
      },
      handleSnapshotRequest: async (_request, callback) => {
        this.log.info('BTicino HOMETOUCH snapshot richiesto da HomeKit');
        const snapStarted = Date.now();
        const snapAsked = JSON.stringify(_request);
        try {
          const image = await new Promise((resolve, reject) => {
            const req = http.get('http://127.0.0.1:8766/snapshot.jpg', res => {
              const chunks = []; let size = 0;
              res.on('data', chunk => { size += chunk.length; if (size > 5 * 1024 * 1024) req.destroy(new Error('Snapshot too large')); else chunks.push(chunk); });
              res.on('error', reject);
              res.on('end', () => res.statusCode === 200 ? resolve(Buffer.concat(chunks)) : reject(new Error('Snapshot unavailable')));
            });
            req.setTimeout(5000, () => req.destroy(new Error('Snapshot timeout')));
            req.on('error', reject);
          });
          let output = image, note = 'originale';
          const {width, height} = _request || {};
          if (Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0) {
            try {
              output = await resizeSnapshot(this.config.ffmpegPath || 'ffmpeg', image, width, height);
              note = `ridimensionato ${width}x${height}`;
            } catch (err) {
              note = `originale (resize fallito: ${err.message})`;
            }
          }
          this.log.info(`Snapshot inviato: richiesta=${snapAsked} ${note} byte=${output.length} in ${Date.now() - snapStarted} ms`);
          callback(null, output);
        } catch (err) {
          this.log.warn(`Snapshot fallito: richiesta=${snapAsked} errore=${err.message} dopo ${Date.now() - snapStarted} ms`);
          callback(err);
        }
      },
    };
    if (this.config.enableHapLive === true) {
      delegate.prepareStream = (req, callback) => {
        if(this.config.enableTwoWayAudio===true) {
          this.streamManager.talkEnabled=false;
          this.controllers[0]?.setSpeakerMuted(true);
        }
        this.log.info('BTicino HOMETOUCH prepareStream richiesto da HomeKit');
        // An exception thrown by HAP's callback must not call that callback twice.
        return this.streamManager.prepare(req).then(response => callback(null, response), callback);
      };
      delegate.handleStreamRequest = (req, callback) => this.streamManager.handle(req).then(() => callback(), err => { this.log.error(`HomeKit stream: ${err.message}`); callback(err); });
    }
    const Controller = this.config.cameraOnly ? this.api.hap.CameraController : this.api.hap.DoorbellController;
    const controller = new Controller({
      name,
      externalDoorbellService: this.doorbellService,
      cameraStreamCount: 1,
      streamingOptions: {
        proxy: false,
        supportedCryptoSuites: [0],
        ...(this.config.enableTwoWayAudio === true ? {
          audio: {codecs:[{type:'OPUS',samplerate:24,audioChannels:1}],twoWayAudio:true},
        } : {}),
        video: {
          codec: { profiles: [0, 1, 2], levels: [0, 1, 2] },
          // FFmpeg scales the source to the resolution negotiated by HomeKit.
          resolutions: [[1280, 720, 30], [640, 480, 30], [640, 360, 30],
            [320, 240, 30], [320, 240, 15], [320, 180, 30]],
        },
      },
      delegate,
    });
    if(this.config.enableTwoWayAudio===true)controller.on('speaker-change',(muted)=>{
      this.streamManager.setTalkEnabled(!muted).catch(error=>this.log.error(`HomeKit talk: ${error.message}`));
    });
    this.controllers.push(controller);
    this.log.info(`BTicino HOMETOUCH camera service published: ${name}`);
  }

  poll(socket) {
    this.timer = setInterval(() => {
      request(socket, 'status').then((result) => {
        this.log.debug(`BTicino HOMETOUCH state: ${result.state}`);
      }).catch(() => {});
      request(socket, 'get_event').then((result) => {
        const eventKey = result.event ? JSON.stringify(result.event) : null;
        if (eventKey && eventKey !== this.lastEventKey) {
          this.lastEventKey = eventKey;
          this.lastEvent = result.event;
          this.log.info(`BTicino HOMETOUCH event: ${result.event.type}`);
          if (result.event.type === 'ring' && this.doorbellService) {
            const event = this.api.hap.Characteristic.ProgrammableSwitchEvent.SINGLE_PRESS;
            const characteristic=this.doorbellService.getCharacteristic(this.api.hap.Characteristic.ProgrammableSwitchEvent);
            this.log.info(`HomeKit ring dispatch: subscribers=${characteristic.subscriptions}`);
            characteristic.sendEventNotification(event);
            this.log.info('HomeKit ring dispatched (phone delivery unconfirmed)');
          }
        }
      }).catch(() => {});
    }, 1000);
  }
}
