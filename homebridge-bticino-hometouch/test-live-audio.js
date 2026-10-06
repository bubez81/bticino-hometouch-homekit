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
const {LiveAudio}=require('./live-audio');
const {randomSsrc}=require('./two-way-audio');
const ffmpeg=process.env.BTICINO_TEST_FFMPEG||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
const processes=[];
function run(args){const child=spawn(ffmpeg,args,{stdio:['ignore','pipe','pipe']});processes.push(child);return child;}
async function bound(port=0){const socket=dgram.createSocket('udp4');await new Promise((r,j)=>{socket.once('error',j);socket.bind(port,'127.0.0.1',r);});return socket;}
async function freePair(){for(;;){const a=await bound();const port=a.address().port;a.close();if(port<65534)return port;}}
function alaw(byte){let a=byte^0x55,t=(a&0x0f)<<4;const seg=(a&0x70)>>4;t=seg===0?t+8:seg===1?t+0x108:(t+0x108)<<(seg-1);return (a&0x80)?t:-t;}
function frequency(samples,rate){let n=0;for(let i=1;i<samples.length;i++)if(samples[i-1]<=0&&samples[i]>0)n++;return n/(samples.length/rate);}
(async()=>{
  const key=crypto.randomBytes(16),salt=crypto.randomBytes(14);
  const homekitPort=await freePair();
  const talkSocket=await bound();
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-live-audio-test-'));
  for(let i=0;i<10000;i++){const v=randomSsrc();assert(Number.isInteger(v)&&v>=1&&v<=0x7fffffff);}
  // Largest SSRC FFmpeg accepts: a larger random value used to break about half the sessions.
  const audio=new LiveAudio({ffmpeg,log:console,key,salt,pt:110,ssrc:0x7fffffff,packetTime:20,homekitPort,talkPort:talkSocket.address().port});
  let timeout;
  try{
    // HomeKit side: decode what the plugin sends.
    const homeSdp=path.join(directory,'home.sdp');
    fs.writeFileSync(homeSdp,sdp({codec:'OPUS',rate:24000,channels:1,port:homekitPort,pt:110,key,salt}),{mode:0o600});
    const home=run(['-hide_banner','-loglevel','error','-protocol_whitelist','file,udp,rtp,srtp,crypto','-probesize','32768','-analyzeduration','0',
      '-i',homeSdp,'-t','0.5','-ar','8000','-ac','1','-f','s16le','pipe:1']);
    const homeChunks=[];home.stdout.on('data',d=>homeChunks.push(d));
    const heard=new Promise((resolve,reject)=>{home.once('exit',code=>code===0?resolve(Buffer.concat(homeChunks)):reject(Error(`HomeKit decoder exit ${code}`)));});
    await audio.start();
    assert.equal(fs.statSync(path.join(audio.directory,'talk.sdp')).mode&0o777,0o600);
    // Entrance panel: 440 Hz as the camera call's raw PCM.
    run(['-hide_banner','-loglevel','error','-re','-f','lavfi','-i','sine=frequency=440:sample_rate=16000','-t','10',
      '-f','s16le','-ac','1',`udp://127.0.0.1:${audio.panelPort}?pkt_size=640`]);
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
    const homeSamples=[];for(let i=0;i+1<homePcm.length;i+=2)homeSamples.push(homePcm.readInt16LE(i));
    const talkSamples=[...talkAlaw.subarray(1600)].map(alaw);
    assert(homeSamples.some(v=>v!==0)&&talkSamples.some(v=>Math.abs(v)>100));
    assert(Math.abs(frequency(homeSamples,8000)-440)<35,`HomeKit heard ${frequency(homeSamples,8000)} Hz`);
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
    clearTimeout(timeout);await audio.stop();
    assert.equal(audio.directory,null);
    try{talkSocket.close();}catch(_){}
    for(const child of processes)if(child.exitCode===null)child.kill('SIGKILL');
    fs.rmSync(directory,{recursive:true,force:true});
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
