'use strict';
// Two independent SRTP directions. Endpoints are private loopback relays, not
// arbitrary network destinations; callers own SIP/HAP negotiation and consent.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');

// FFmpeg's RTP muxer takes -ssrc as a signed 32-bit option: values above
// 2^31-1 make it fail ("Result too large"), so stay in 1..2^31-1.
function randomSsrc() {
  return (require('node:crypto').randomBytes(4).readUInt32BE() & 0x7fffffff) || 1;
}
const CODECS = {
  OPUS: {encoder:'libopus',clock:48000,rates:[16000,24000,48000]},
  PCMU: {encoder:'pcm_mulaw',clock:8000,rates:[8000]},
  PCMA: {encoder:'pcm_alaw',clock:8000,rates:[8000]},
  SPEEX: {encoder:'libspeex',clock:8000,rates:[8000]},
};
function validate(endpoint) {
  const codec = CODECS[endpoint.codec];
  if (!codec || !codec.rates.includes(endpoint.rate) || endpoint.channels !== 1 ||
      !Number.isInteger(endpoint.port) || endpoint.port<1024 || endpoint.port>65534 ||
      !Number.isInteger(endpoint.pt) || endpoint.pt<0 || endpoint.pt>127 ||
      !Buffer.isBuffer(endpoint.key) || endpoint.key.length!==16 ||
      !Buffer.isBuffer(endpoint.salt) || endpoint.salt.length!==14) throw Error('Unsupported audio negotiation');
  return codec;
}
function sdp(endpoint) {
  const codec=validate(endpoint);
  // RFC 7587: Opus RTP clock is always 48 kHz, including 24-kHz mono audio.
  const mapping=endpoint.codec==='OPUS' ? 'opus/48000/2' : `${endpoint.codec}/${codec.clock}`;
  return `v=0\no=- 0 0 IN IP4 127.0.0.1\ns=Private audio\nc=IN IP4 127.0.0.1\nt=0 0\nm=audio ${endpoint.port} RTP/SAVP ${endpoint.pt}\na=rtcp:${endpoint.port+1}\na=rtpmap:${endpoint.pt} ${mapping}\na=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:${Buffer.concat([endpoint.key,endpoint.salt]).toString('base64')}\na=recvonly\n`;
}
class TwoWayAudio {
  constructor(ffmpeg,log) {this.ffmpeg=ffmpeg;this.log=log;this.processes=[];}
  async start({homeInput,doorInput,homeOutput,doorOutput}) {
    if(this.directory)throw Error('Audio already started');
    for(const endpoint of [homeInput,doorInput,homeOutput,doorOutput])validate(endpoint);
    for(const endpoint of [homeOutput,doorOutput])if(!Number.isInteger(endpoint.ssrc)||endpoint.ssrc<1||endpoint.ssrc>0xffffffff)throw Error('Invalid audio SSRC');
    this.directory=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-audio-'));
    fs.chmodSync(this.directory,0o700);
    try {
      await this.direction('listen',doorInput,homeOutput);
      await this.direction('talk',homeInput,doorOutput);
    } catch(error) {await this.stop();throw error;}
  }
  async direction(name,input,output) {
    const file=path.join(this.directory,`${name}.sdp`);
    fs.writeFileSync(file,sdp(input),{mode:0o600,flag:'wx'});
    const args=['-hide_banner','-loglevel','error','-nostdin','-protocol_whitelist','file,udp,rtp,srtp,crypto',
      '-probesize','32768','-analyzeduration','0','-i',file,'-map','0:a:0','-vn',
      '-c:a',CODECS[output.codec].encoder,'-ar',String(output.rate),'-ac','1'];
    if(output.codec==='OPUS')args.push('-application','lowdelay','-frame_duration','20','-b:a','24k');
    if(output.codec==='SPEEX')args.push('-frames_per_packet','1','-vad','0','-dtx','0');
    args.push('-payload_type',String(output.pt),'-ssrc',String(output.ssrc),'-f','rtp',
      '-srtp_out_suite','AES_CM_128_HMAC_SHA1_80','-srtp_out_params',Buffer.concat([output.key,output.salt]).toString('base64'),
      `srtp://127.0.0.1:${output.port}?rtcpport=${output.port+1}&pkt_size=1200`);
    const process=spawn(this.ffmpeg,args,{stdio:['ignore','ignore','pipe']});
    this.processes.push(process);
    // Do not print FFmpeg arguments or raw stderr, which may contain SDES keys.
    process.stderr.on('data',()=>{this.log?.warn?.(`BTicino audio ${name}: encoder diagnostic received`);});
    process.on('exit',(code,signal)=>{if(!this.stopping)this.log?.warn?.(`BTicino audio ${name} ended: ${code ?? signal}`);});
    await new Promise((resolve,reject)=>{process.once('spawn',resolve);process.once('error',reject);});
  }
  async stop() {
    if(this.stopPromise)return this.stopPromise;
    this.stopping=true;
    this.stopPromise=(async()=>{
      await Promise.all(this.processes.map(process=>new Promise(resolve=>{
        if(process.exitCode!==null||process.signalCode!==null)return resolve();
        const timer=setTimeout(()=>process.kill('SIGKILL'),2000);
        process.once('exit',()=>{clearTimeout(timer);resolve();});process.kill('SIGTERM');
      })));
      if(this.directory){
        for(const name of ['listen.sdp','talk.sdp']){const file=path.join(this.directory,name);if(fs.existsSync(file))fs.unlinkSync(file);}
        fs.rmdirSync(this.directory);this.directory=null;
      }
    })();
    return this.stopPromise;
  }
}
module.exports={TwoWayAudio,sdp,validate,randomSsrc};
