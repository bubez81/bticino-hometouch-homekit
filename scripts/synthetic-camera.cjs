'use strict';
// Independent HAP control experiment: no BTicino code, SIP or production storage.
const hap = require('/usr/local/lib/node_modules/homebridge/node_modules/@homebridge/hap-nodejs');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const dgram = require('node:dgram');
const ffmpeg = '/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
const sessions = new Map();
const storage = process.env.SYNTHETIC_STORAGE;
if (!storage || !path.isAbsolute(storage)) throw Error('Set SYNTHETIC_STORAGE to an isolated absolute directory');
hap.HAPStorage.setCustomStoragePath(storage);
function stop(id) {
  const session = sessions.get(id);
  if (!session) return;
  sessions.delete(id);
  clearTimeout(session.timer);
  if (session.child) session.child.kill('SIGTERM');
  session.socket.close();
}
function snapshot(request, callback) {
  console.log('[SYNTHETIC] snapshot requested');
  const width = Math.min(1280, Math.max(16, Number(request.width) || 640));
  const height = Math.min(720, Math.max(16, Number(request.height) || 480));
  const child = spawn(ffmpeg, ['-hide_banner','-loglevel','error','-f','lavfi','-i',`testsrc2=size=${width}x${height}`, '-frames:v','1','-f','image2pipe','-vcodec','mjpeg','pipe:1'], {stdio:['ignore','pipe','ignore']});
  const chunks = [];
  let done = false;
  const finish = (err, data) => { if (!done) { done = true; clearTimeout(timer); callback(err, data); } };
  const timer = setTimeout(() => {child.kill('SIGKILL');finish(Error('Snapshot timeout'));}, 8000);
  child.stdout.on('data', data => chunks.push(data));
  child.on('error', err => finish(err));
  child.on('exit', code => finish(code === 0 ? null : Error('Snapshot failed'), Buffer.concat(chunks)));
}
const delegate = {
  handleSnapshotRequest: snapshot,
  prepareStream(request, callback) {
    console.log('[SYNTHETIC] prepareStream');
    const socket = dgram.createSocket(request.addressVersion === 'ipv6' ? 'udp6' : 'udp4');
    const session = {socket, request, ssrc:hap.CameraController.generateSynchronisationSource()};
    sessions.set(request.sessionID, session);
    socket.once('error', err => {stop(request.sessionID);callback(err);});
    socket.bind(0, () => {
      session.timer = setTimeout(() => stop(request.sessionID), 120000);
      callback(null, {video:{port:socket.address().port,ssrc:session.ssrc,srtp_key:request.video.srtp_key,srtp_salt:request.video.srtp_salt}});
    });
  },
  handleStreamRequest(request, callback) {
    console.log('[SYNTHETIC] stream', request.type);
    if (request.type === 'stop') {stop(request.sessionID);callback();return;}
    const session = sessions.get(request.sessionID);
    if (!session || request.type !== 'start') {callback(Error('Unsupported session request'));return;}
    const v = request.video;
    const host = session.request.addressVersion === 'ipv6' ? `[${session.request.targetAddress}]` : session.request.targetAddress;
    const child = session.child = spawn(ffmpeg, ['-hide_banner','-loglevel','error','-nostdin','-re','-f','lavfi','-i',`testsrc2=size=${v.width}x${v.height}:rate=${v.fps}`, '-an','-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-pix_fmt','yuv420p','-profile:v',['baseline','main','high'][v.profile] || 'baseline','-level:v',['3.1','3.2','4.0'][v.level] || '3.1','-b:v',`${v.max_bit_rate}k`,'-g',String(v.fps*2),'-payload_type',String(v.pt),'-ssrc',String(session.ssrc),'-f','rtp','-srtp_out_suite','AES_CM_128_HMAC_SHA1_80','-srtp_out_params',Buffer.concat([session.request.video.srtp_key,session.request.video.srtp_salt]).toString('base64'),`srtp://${host}:${session.request.video.port}?rtcpport=${session.request.video.port}&pkt_size=${Math.min(v.mtu,1316)}`], {stdio:'ignore'});
    child.once('spawn', () => callback());
    child.once('error', err => {stop(request.sessionID);callback(err);});
    child.once('exit', code => {console.log('[SYNTHETIC] encoder exit',code);stop(request.sessionID);});
  },
};
if (process.env.BTICINO_CONTROL_TEST === '1') {
  const {StreamManager} = require('/private/tmp/bticino-child/plugin/stream');
  const manager = new StreamManager('/tmp/bticino-hometouch.sock', console, {ffmpegPath:ffmpeg});
  delegate.prepareStream = (req, cb) => {console.log('[BTICINO-CONTROL] prepare');manager.prepare(req).then(r=>cb(null,r)).catch(cb);};
  delegate.handleStreamRequest = (req, cb) => {console.log('[BTICINO-CONTROL]',req.type);manager.handle(req).then(()=>cb()).catch(e=>{console.error(e.message);cb(e);});};
  delegate.handleSnapshotRequest = (_req, cb) => {
    const http = require('node:http');
    const request = http.get('http://127.0.0.1:8766/snapshot.jpg', response => {
      const chunks=[];response.on('data',d=>chunks.push(d));response.on('end',()=>cb(response.statusCode===200?null:Error('Snapshot HTTP failed'),Buffer.concat(chunks)));
    });
    request.setTimeout(5000,()=>request.destroy(Error('Snapshot timeout')));request.on('error',cb);
  };
  process.once('SIGINT',()=>manager.closeAll());
  process.once('SIGTERM',()=>manager.closeAll());
}
if (process.argv.includes('--self-test')) {
  snapshot({width:320,height:240}, (err, data) => {
    if (err) {console.error(err.message);process.exitCode=1;return;}
    if (data[0] !== 255 || data[1] !== 216) throw Error('Invalid JPEG');
    console.log('SYNTHETIC_JPEG_OK bytes=' + data.length);
  });
} else {
  const bridge = new hap.Bridge('Video Sintetico Test',hap.uuid.generate('bticino-independent-synthetic-bridge-v1'));
  const camera = new hap.Accessory('Test Barre Colore',hap.uuid.generate('bticino-independent-synthetic-camera-v1'));
  if (process.env.BTICINO_PLUGIN_TEST === '1') {
    let Constructor;
    const {EventEmitter}=require('node:events');
    const api=new EventEmitter();api.hap=hap;api.registerAccessory=(_p,_n,c)=>{Constructor=c;};
    require('/private/tmp/bticino-child/plugin/index.js')(api);
    const instance=new Constructor(console,{name:'Test Barre Colore',cameraOnly:true,enableCamera:true,enableHapLive:true,ffmpegPath:ffmpeg},api);
    instance.getServices().forEach(s=>camera.addService(s));
    instance.getControllers().forEach(c=>camera.configureController(c));
    process.once('SIGINT',()=>api.emit('shutdown'));
    process.once('SIGTERM',()=>api.emit('shutdown'));
  } else camera.configureController(new hap.CameraController({cameraStreamCount:2,delegate,streamingOptions:{supportedCryptoSuites:[0],video:{codec:{profiles:[0,1,2],levels:[0,1,2]},resolutions:[[1280,720,30],[640,480,30],[640,360,30],[320,240,30],[320,240,15],[320,180,30]]}}}));
  bridge.addBridgedAccessory(camera);
  bridge.publish({username:'0E:12:34:56:78:9B',pincode:'031-45-154',port:51990,category:hap.Categories.BRIDGE}).catch(err => {console.error(err.message);process.exitCode=1;});
  bridge.on('listening', () => console.log('SYNTHETIC_READY port=51990 code=031-45-154'));
  for (const signal of ['SIGINT','SIGTERM']) process.once(signal, async () => {for(const id of [...sessions.keys()])stop(id);await bridge.unpublish();process.exit(0);});
}
