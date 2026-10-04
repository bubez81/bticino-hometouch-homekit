'use strict';

const { request } = require('./ipc');
const http = require('node:http');
const { StreamManager } = require('./stream');
const { installCallUnlock } = require('./call-unlock');
const managers = new WeakMap();
const cameraKey = config => JSON.stringify([config.ipcSocket || '/tmp/bticino-hometouch.sock', config.name || 'BTicino HOMETOUCH']);

/**
 * Bridged BTicino HOMETOUCH accessory.
 * The accessory owns the doorbell and camera services; SIP/RTP remains in the
 * existing listener and is reached through the local IPC socket.
 */
module.exports = (api) => {
  managers.set(api,new Map());
  api.registerAccessory('homebridge-bticino-hometouch', 'BTicinoHOMETOUCH', BTicinoAccessory);
  api.registerAccessory('homebridge-bticino-hometouch', 'BTicinoCallLock', BTicinoCallLock);
};

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
    this.api.on('shutdown', () => { if (this.timer) clearInterval(this.timer); this.streamManager.closeAll().catch(err => this.log.error(err.message)); });
    this.didFinishLaunching();
  }

  getServices() { return [...(this.config.cameraOnly ? [] : [this.doorbellService]),...(this.unlockService ? [this.unlockService] : [])]; }
  getControllers() { return this.controllers; }

  didFinishLaunching() {
    const socket = this.config.ipcSocket || '/tmp/bticino-hometouch.sock';
    request(socket, 'ping').then(() => {
      this.log.info('BTicino HOMETOUCH IPC connected');
      this.poll(socket);
    }).catch(() => this.log.warn('BTicino HOMETOUCH IPC unavailable; listener may be stopped'));
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
          callback(null, image);
        } catch (err) { callback(err); }
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
