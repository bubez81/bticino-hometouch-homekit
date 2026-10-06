'use strict';
const dgram=require('node:dgram');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const ipc=require('./ipc');
const {LiveAudio,TALK_PORT}=require('./live-audio');

async function pair() {
  for(let attempt=0;attempt<20;attempt++) {
    const sockets=[dgram.createSocket('udp4'),dgram.createSocket('udp4')];
    try {
      await new Promise((resolve,reject)=>{sockets[0].once('error',reject);sockets[0].bind(0,'127.0.0.1',resolve);});
      const port=sockets[0].address().port;
      await new Promise((resolve,reject)=>{sockets[1].once('error',reject);sockets[1].bind(port+1,'127.0.0.1',resolve);});
      return {port,sockets,close(){for(const socket of sockets)try{socket.close();}catch{}}};
    } catch {for(const socket of sockets)try{socket.close();}catch{}}
  }
  throw Error('No local media ports available');
}
function material(value) {
  const bytes=Buffer.from(value,'base64');
  if(bytes.length!==30)throw Error('Invalid incoming media key');
  return {key:bytes.subarray(0,16),salt:bytes.subarray(16)};
}

class IncomingCall {
  constructor(manager,session,request) {
    Object.assign(this,{manager,session,request});this.pairs=[];
  }
  async start() {
    const {manager,session:s,request:req}=this;
    if(req.audio?.codec!=='OPUS'||req.audio.channel!==1||req.audio.sample_rate!==24)throw Error('Unsupported HomeKit audio');
    try {
      for(let i=0;i<3;i++)this.pairs.push(await pair());
      const [video,door,out]=this.pairs;
      // Same audio path as the on-demand live view: the listener decodes the
      // panel to PCM and sends silence or our speech; we re-encode for HomeKit
      // and pass the iPhone microphone to the listener's talk port.
      this.audio=new LiveAudio({ffmpeg:manager.config.audioFfmpegPath||manager.config.ffmpegPath||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg',
        log:manager.log,key:s.req.audio.srtp_key,salt:s.req.audio.srtp_salt,pt:req.audio.pt,ssrc:s.audioSsrc,
        packetTime:req.audio.packet_time,homekitPort:out.port,talkPort:manager.config.talkPort||TALK_PORT});
      await this.audio.start();
      const result=await ipc.request(manager.socketPath,'attach_incoming',{
        session_id:s.id,video_port:video.port,audio_port:door.port,pcm_port:this.audio.panelPort},4000);
      if(!result.ok)throw Error(result.error);
      this.attached=true;
      if(!result.pcm)manager.log.warn('BTicino incoming call: the listener offers no panel audio');
      this.directory=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-incoming-'));
      fs.chmodSync(this.directory,0o700);
      this.videoPath=path.join(this.directory,'video.sdp');
      material(result.video.material);
      if(!/^\d+$/.test(String(result.video.payload))||/[\r\n]/.test(result.video.fmtp||''))throw Error('Invalid incoming video');
      fs.writeFileSync(this.videoPath,`v=0\no=- 0 0 IN IP4 127.0.0.1\ns=Incoming doorbell\nc=IN IP4 127.0.0.1\nt=0 0\nm=video ${video.port} RTP/SAVP ${result.video.payload}\na=rtpmap:${result.video.payload} H264/90000\na=rtcp:${video.port+1}\na=fmtp:${result.video.payload} ${result.video.fmtp||''}\na=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:${result.video.material}\n`,{mode:0o600,flag:'wx'});
      video.close();door.close();
      for(const socket of out.sockets)socket.on('message',(packet,peer)=>{
        if(peer.address==='127.0.0.1')s.audioSocket.send(packet,s.req.audio.port,s.req.targetAddress,()=>{});
      });
      this.ready=true;
    }catch(error){await this.close();throw error;}
  }
  receive(packet,peer) {
    const s=this.session;
    if(!this.ready||peer.address!==s.req.targetAddress||peer.port!==s.req.audio.port)return;
    // iPhone microphone: reaches the door only while Home's speaker is unmuted.
    if(this.talkEnabled)this.audio.receiveTalk(packet);
  }
  async talk(enabled) {
    if(!this.ready)return;
    const result=await ipc.request(this.manager.socketPath,'answer_incoming',{session_id:this.session.id,enabled},4000);
    if(!result.ok)throw Error(result.error);
    this.talkEnabled=enabled;
  }
  async answerMuted() {
    if(!this.ready)throw Error('Incoming media not ready');
    const result=await ipc.request(this.manager.socketPath,'answer_incoming',{
      session_id:this.session.id,enabled:false,answer:true},4000);
    if(!result.ok)throw Error(result.error);
    this.talkEnabled=false;
    this.manager.log.info('HomeKit incoming call accepted; microphone remains muted');
  }
  async close() {
    if(this.closePromise)return this.closePromise;
    this.closePromise=this.cleanup();
    return this.closePromise;
  }
  async cleanup() {
    this.ready=false;this.talkEnabled=false;
    await this.audio?.stop();
    if(this.attached){this.attached=false;await ipc.request(this.manager.socketPath,'release_incoming',{session_id:this.session.id},4000).catch(()=>{});}
    for(const pair of this.pairs)pair.close();
    if(this.directory){if(fs.existsSync(this.videoPath))fs.unlinkSync(this.videoPath);fs.rmdirSync(this.directory);this.directory=null;}
  }
}
module.exports={IncomingCall,pair,material};
