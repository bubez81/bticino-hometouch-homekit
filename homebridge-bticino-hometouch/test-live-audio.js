'use strict';
// Real encoders, no network: the panel's PCM reaches HomeKit as SRTP Opus,
// and HomeKit SRTP Opus reaches the call's talk port as A-law.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const dgram=require('node:dgram');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {sdp}=require('./two-way-audio');
const {LiveAudio,JitterBuffer}=require('./live-audio');
const {randomSsrc}=require('./two-way-audio');
const ffmpeg=process.env.BTICINO_TEST_FFMPEG||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
const processes=[];
function run(args){const child=spawn(ffmpeg,args,{stdio:['ignore','pipe','pipe']});processes.push(child);return child;}
async function bound(port=0){const socket=dgram.createSocket('udp4');await new Promise((r,j)=>{socket.once('error',j);socket.bind(port,'127.0.0.1',r);});return socket;}
async function freePair(){for(;;){const a=await bound();const port=a.address().port;a.close();if(port<65534)return port;}}
function alaw(byte){let a=byte^0x55,t=(a&0x0f)<<4;const seg=(a&0x70)>>4;t=seg===0?t+8:seg===1?t+0x108:(t+0x108)<<(seg-1);return (a&0x80)?t:-t;}
function frequency(samples,rate){let n=0;for(let i=1;i<samples.length;i++)if(samples[i-1]<=0&&samples[i]>0)n++;return n/(samples.length/rate);}
function jitterChecks() {
  let now=0;
  const jitter=new JitterBuffer({clock:()=>now});
  assert.equal(jitter.take().length,640);            // start: one frame of silence
  jitter.push(Buffer.alloc(3200,1));                 // 100 ms: not yet primed
  now=20; assert(jitter.take().every(b=>b===0));
  jitter.push(Buffer.alloc(3200,1));                 // 200 ms buffered: primed
  now=40; const audio=jitter.take(); assert.equal(audio.length,640); assert(audio.every(b=>b===1));
  now=1000; const late=jitter.take();               // panel silent for ~1 s
  assert.equal(late.length%640,0); assert(jitter.underruns===1&&jitter.primed===false);
  assert.equal(jitter.written,Math.floor(Math.round(1000*32)/640)*640+640);
  jitter.push(Buffer.alloc(40000,2));                // burst: keep only the target
  assert.equal(jitter.pending.length,6400); assert(jitter.dropped>0);
  const r=jitter.report(); assert(r.received>0&&r.underruns===1&&r.dropped>0&&r.level<0);
  assert.deepEqual(jitter.report(),{received:0,underruns:0,dropped:0,level:null});
}
(async()=>{
  jitterChecks();
  const key=crypto.randomBytes(16),salt=crypto.randomBytes(14);
  const homekitPort=await freePair();
  const talkSocket=await bound();
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-live-audio-test-'));
  for(let i=0;i<10000;i++){const v=randomSsrc();assert(Number.isInteger(v)&&v>=1&&v<=0x7fffffff);}
  // Largest SSRC FFmpeg accepts: a larger random value used to break about half the sessions.
  const audio=new LiveAudio({ffmpeg,log:console,key,salt,pt:110,ssrc:0x7fffffff,packetTime:20,homekitPort,talkPort:talkSocket.address().port});
  let timeout,burst;
  try{
    // HomeKit side: decode what the plugin sends.
    const homeSdp=path.join(directory,'home.sdp');
    fs.writeFileSync(homeSdp,sdp({codec:'OPUS',rate:24000,channels:1,port:homekitPort,pt:110,key,salt}),{mode:0o600});
    const home=run(['-hide_banner','-loglevel','error','-protocol_whitelist','file,udp,rtp,srtp,crypto','-probesize','32768','-analyzeduration','0',
      '-i',homeSdp,'-t','3','-ar','8000','-ac','1','-f','s16le','pipe:1']);
    const homeChunks=[];home.stdout.on('data',d=>homeChunks.push(d));
    const heard=new Promise((resolve,reject)=>{home.once('exit',code=>code===0?resolve(Buffer.concat(homeChunks)):reject(Error(`HomeKit decoder exit ${code}`)));});
    await audio.start();
    assert.equal(fs.statSync(path.join(audio.directory,'talk.sdp')).mode&0o777,0o600);
    // Entrance panel: 440 Hz raw PCM in bursts like the measured call:
    // 180-ms pauses followed by catch-up, at the right average rate.
    const panel=dgram.createSocket('udp4');let sample=0,sentBytes=0;const t0=Date.now();
    let tick=0;
    burst=setInterval(()=>{
      if(++tick%4!==0)return;                        // send only every 240 ms
      const due=Math.floor((Date.now()-t0)*32)-sentBytes;
      for(let n=0;n+640<=due;n+=640){
        const frame=Buffer.alloc(640);
        for(let i=0;i<320;i++)frame.writeInt16LE(Math.round(8000*Math.sin(2*Math.PI*440*(sample++)/16000)),i*2);
        panel.send(frame,audio.panelPort,'127.0.0.1');sentBytes+=640;
      }
    },60);
    // iPhone microphone: 880 Hz as HomeKit SRTP Opus, delivered through receiveTalk().
    const relay=await bound();
    relay.on('message',packet=>audio.receiveTalk(packet));
    run(['-hide_banner','-loglevel','error','-re','-f','lavfi','-i','sine=frequency=880:sample_rate=24000','-t','10','-ac','1',
      '-c:a','libopus','-application','lowdelay','-frame_duration','20','-payload_type','110','-ssrc','777','-f','rtp',
      '-srtp_out_suite','AES_CM_128_HMAC_SHA1_80','-srtp_out_params',Buffer.concat([key,salt]).toString('base64'),
      `srtp://127.0.0.1:${relay.address().port}?rtcpport=${relay.address().port}`]);
    const talk=new Promise(resolve=>{const bytes=[];talkSocket.on('message',d=>{bytes.push(d);if(Buffer.concat(bytes).length>=8000)resolve(Buffer.concat(bytes));});});
    const [homePcm,talkAlaw]=await Promise.race([Promise.all([heard,talk]),
      new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Live audio timed out')),20000);})]);
    clearInterval(burst);try{panel.close();}catch(_){}
    // Skip the first second: the jitter buffer fills ~200 ms before playing.
    const homeSamples=[];for(let i=16000;i+1<homePcm.length;i+=2)homeSamples.push(homePcm.readInt16LE(i));
    assert(homeSamples.length>=8000,`HomeKit got ${homeSamples.length} samples`);
    const talkSamples=[...talkAlaw.subarray(1600)].map(alaw);
    assert(homeSamples.some(v=>v!==0)&&talkSamples.some(v=>Math.abs(v)>100));
    assert(Math.abs(frequency(homeSamples,8000)-440)<35,`HomeKit heard ${frequency(homeSamples,8000)} Hz`);
    // Continuous: no run of silence longer than 20 ms in what HomeKit plays.
    let quiet=0,longest=0;for(const v of homeSamples){quiet=Math.abs(v)<200?quiet+1:0;longest=Math.max(longest,quiet);}
    assert(longest<160,`HomeKit heard a ${longest/8} ms gap`);
    assert(Math.abs(frequency(talkSamples,8000)-880)<35,`door got ${frequency(talkSamples,8000)} Hz`);
    assert(audio.talkPackets>0);
    relay.close();
    // A converter that ends during the live view is started again.
    const before=audio.processes.length;
    audio.processes[audio.processes.length-1].kill('SIGKILL');
    await new Promise(resolve=>setTimeout(resolve,1800));
    assert.equal(audio.processes.length,before);
    assert(audio.processes.every(child=>child.exitCode===null));
    console.log('LIVE_AUDIO_OK: panel 440Hz -> HomeKit Opus; HomeKit 880Hz -> door A-law');
  }finally{
    clearTimeout(timeout);clearInterval(burst);await audio.stop();
    assert(!audio.directory);
    try{talkSocket.close();}catch(_){}
    for(const child of processes)if(child.exitCode===null)child.kill('SIGKILL');
    fs.rmSync(directory,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
