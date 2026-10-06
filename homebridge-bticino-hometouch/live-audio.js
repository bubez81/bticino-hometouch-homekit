'use strict';
// Real audio for on-demand HomeKit live view.
// listen: the camera call writes the entrance panel's audio as raw 16-kHz PCM
//         to a loopback port; FFmpeg encodes it as HomeKit's SRTP Opus.
// talk:   HomeKit's SRTP Opus from the iPhone is decoded to 8-kHz A-law and
//         sent to the camera call's talk port, which sends it to the door as
//         Speex instead of silence.
// Keys stay in private SDP files or FFmpeg arguments, never in logs.
const dgram=require('node:dgram');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {sdp}=require('./two-way-audio');

const TALK_PORT=22310;
function redact(text) {
  return text.replace(/inline:\S+/g,'inline:[redacted]').replace(/[A-Za-z0-9+/]{24,}={0,2}/g,'[redacted]');
}

async function freePort(pair=false) {
  for(let attempt=0;attempt<20;attempt++){
    const sockets=[dgram.createSocket('udp4')];
    try{
      await new Promise((resolve,reject)=>{sockets[0].once('error',reject);sockets[0].bind(0,'127.0.0.1',resolve);});
      const port=sockets[0].address().port;
      if(pair){
        if(port>=65534)continue;
        sockets.push(dgram.createSocket('udp4'));
        await new Promise((resolve,reject)=>{sockets[1].once('error',reject);sockets[1].bind(port+1,'127.0.0.1',resolve);});
      }
      return port;
    }catch(_){}
    finally{for(const socket of sockets)try{socket.close();}catch(_){}}
  }
  throw Error('No local audio ports available');
}

class LiveAudio {
  constructor({ffmpeg,log,key,salt,pt,ssrc,packetTime,homekitPort,talkPort=TALK_PORT}) {
    if(!Buffer.isBuffer(key)||key.length!==16||!Buffer.isBuffer(salt)||salt.length!==14)throw Error('Invalid HomeKit audio key');
    for(const n of [pt,ssrc,homekitPort,talkPort])if(!Number.isInteger(n)||n<0)throw Error('Invalid HomeKit audio parameters');
    Object.assign(this,{ffmpeg,log,key,salt,pt,ssrc,homekitPort,talkPort,processes:[]});
    this.packetTime=[20,30,40,60].includes(packetTime)?packetTime:20;
    this.sender=dgram.createSocket('udp4');
  }
  async start() {
    this.panelPort=await freePort();
    this.talkInput=await freePort(true);
    this.directory=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-live-audio-'));
    fs.chmodSync(this.directory,0o700);
    const talkSdp=path.join(this.directory,'talk.sdp');
    fs.writeFileSync(talkSdp,sdp({codec:'OPUS',rate:24000,channels:1,port:this.talkInput,pt:this.pt,key:this.key,salt:this.salt}),{mode:0o600,flag:'wx'});
    const params=Buffer.concat([this.key,this.salt]).toString('base64');
    this.spawn('listen',['-hide_banner','-loglevel','error','-nostdin','-fflags','nobuffer','-flags','low_delay',
      '-f','s16le','-ar','16000','-ac','1','-i',`udp://127.0.0.1:${this.panelPort}?fifo_size=4096&overrun_nonfatal=1`,
      '-c:a','libopus','-ar','24000','-ac','1','-b:a','24k','-application','lowdelay','-frame_duration',String(this.packetTime),
      '-payload_type',String(this.pt),'-ssrc',String(this.ssrc),'-f','rtp','-srtp_out_suite','AES_CM_128_HMAC_SHA1_80',
      '-srtp_out_params',params,`srtp://127.0.0.1:${this.homekitPort}?rtcpport=${this.homekitPort}&pkt_size=1200`]);
    this.spawn('talk',['-hide_banner','-loglevel','error','-nostdin','-protocol_whitelist','file,udp,rtp,srtp,crypto',
      '-probesize','32768','-analyzeduration','0','-i',talkSdp,'-map','0:a:0','-vn',
      '-c:a','pcm_alaw','-ar','8000','-ac','1','-f','alaw',`udp://127.0.0.1:${this.talkPort}?pkt_size=320`]);
    await Promise.all(this.processes.map(child=>new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);})));
  }
  spawn(name,args) {
    const child=spawn(this.ffmpeg,args,{stdio:['ignore','ignore','pipe']});
    // Log the first FFmpeg messages with anything key-like removed.
    let lines=0;
    child.stderr.on('data',data=>{
      for(const line of data.toString().split('\n')){
        if(!line.trim()||lines>=5)continue;
        lines++;
        this.log?.warn?.(`BTicino live audio ${name}: ${redact(line).slice(0,300)}`);
      }
    });
    child.on('exit',(code,signal)=>{if(!this.stopping)this.log?.warn?.(`BTicino live audio ${name} ended: ${code ?? signal}`);});
    this.processes.push(child);
  }
  // HomeKit microphone packets (SRTP/SRTCP) for the talk decoder.
  receiveTalk(packet) {
    if(this.stopping||!this.talkInput||packet.length<12)return;
    const rtcp=packet[1]>=192&&packet[1]<=223;
    this.talkPackets=(this.talkPackets||0)+(rtcp?0:1);
    this.sender.send(packet,this.talkInput+(rtcp?1:0),'127.0.0.1',()=>{});
  }
  async stop() {
    if(this.stopPromise)return this.stopPromise;
    this.stopping=true;
    this.stopPromise=(async()=>{
      await Promise.all(this.processes.map(child=>new Promise(resolve=>{
        if(child.exitCode!==null||child.signalCode!==null)return resolve();
        const timer=setTimeout(()=>child.kill('SIGKILL'),2000);
        child.once('exit',()=>{clearTimeout(timer);resolve();});child.kill('SIGTERM');
      })));
      try{this.sender.close();}catch(_){}
      if(this.directory){fs.rmSync(this.directory,{recursive:true,force:true});this.directory=null;}
    })();
    return this.stopPromise;
  }
}
module.exports={LiveAudio,TALK_PORT,redact};
