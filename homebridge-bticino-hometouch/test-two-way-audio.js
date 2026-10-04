'use strict';
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const dgram=require('node:dgram');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawn}=require('node:child_process');
const {TwoWayAudio,sdp,validate}=require('./two-way-audio');
const ffmpeg=process.env.BTICINO_TEST_FFMPEG||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
const processes=[];
async function endpoint(codec,pt,ssrc) {
  for(let i=0;i<30;i++){
    const a=dgram.createSocket('udp4'),b=dgram.createSocket('udp4');
    try{
      await new Promise((r,j)=>{a.once('error',j);a.bind(0,'127.0.0.1',r);});
      const port=a.address().port;
      if(port===65535)continue;
      await new Promise((r,j)=>{b.once('error',j);b.bind(port+1,'127.0.0.1',r);});
      return {codec,pt,ssrc,port,channels:1,rate:codec==='OPUS'?24000:8000,key:crypto.randomBytes(16),salt:crypto.randomBytes(14)};
    }catch(_){}finally{try{a.close();}catch(_){}try{b.close();}catch(_){}}
  }
  throw Error('No test ports available');
}
function run(args){const child=spawn(ffmpeg,args,{stdio:['ignore','pipe','pipe']});processes.push(child);return child;}
function source(endpoint,frequency){
  const args=['-hide_banner','-loglevel','error','-re','-f','lavfi','-i',`sine=frequency=${frequency}:sample_rate=${endpoint.rate}`,
    '-t','12','-ac','1','-c:a',endpoint.codec==='OPUS'?'libopus':endpoint.codec==='SPEEX'?'libspeex':endpoint.codec==='PCMA'?'pcm_alaw':'pcm_mulaw'];
  if(endpoint.codec==='OPUS')args.push('-application','lowdelay','-frame_duration','20');
  return run([...args,'-payload_type',String(endpoint.pt),'-ssrc',String(endpoint.ssrc),'-f','rtp','-srtp_out_suite','AES_CM_128_HMAC_SHA1_80',
    '-srtp_out_params',Buffer.concat([endpoint.key,endpoint.salt]).toString('base64'),`srtp://127.0.0.1:${endpoint.port}?rtcpport=${endpoint.port+1}`]);
}
function sink(endpoint,file){
  fs.writeFileSync(file,sdp(endpoint),{mode:0o600});
  const child=run(['-hide_banner','-loglevel','error','-protocol_whitelist','file,udp,rtp,srtp,crypto','-probesize','32768','-analyzeduration','0','-i',file,
    '-t','0.4','-ar','8000','-ac','1','-f','s16le','pipe:1']);
  const chunks=[];child.stdout.on('data',d=>chunks.push(d));
  return new Promise((resolve,reject)=>{child.once('error',reject);child.once('exit',code=>code===0?resolve(Buffer.concat(chunks)):reject(Error(`Audio decoder exit ${code}`)));});
}
(async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-audio-test-'));
  const manager=new TwoWayAudio(ffmpeg,console);
  const doorCodec=['PCMA','SPEEX'].includes(process.env.BTICINO_TEST_AUDIO_CODEC)?process.env.BTICINO_TEST_AUDIO_CODEC:'PCMU';
  const homeInput=await endpoint('OPUS',110,101),doorInput=await endpoint(doorCodec,doorCodec==='SPEEX'?97:doorCodec==='PCMA'?8:0,102);
  const homeOutput=await endpoint('OPUS',111,103),doorOutput=await endpoint(doorCodec,doorCodec==='SPEEX'?97:doorCodec==='PCMA'?8:0,104);
  assert.throws(()=>validate({...homeInput,key:Buffer.alloc(0)}));
  assert.throws(()=>validate({...homeInput,codec:'G729'}));
  let timeout;
  try{
    const listening=sink(homeOutput,path.join(directory,'home.sdp'));
    const talking=sink(doorOutput,path.join(directory,'door.sdp'));
    await manager.start({homeInput,doorInput,homeOutput,doorOutput});
    assert.equal(fs.statSync(path.join(manager.directory,'talk.sdp')).mode & 0o777,0o600);
    source(homeInput,880);source(doorInput,440);
    const results=await Promise.race([Promise.all([listening,talking]),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Bidirectional audio timed out')),18000);})]);
    for(const pcm of results){assert(pcm.length>=3200);assert(pcm.some(byte=>byte!==0));}
    // Count positive-going zero crossings: distinguish the two directions,
    // rather than accepting silence or an accidental self-loop.
    const frequency=pcm=>{let n=0;for(let i=2;i<pcm.length;i+=2)if(pcm.readInt16LE(i-2)<=0&&pcm.readInt16LE(i)>0)n++;return n/(pcm.length/2/8000);};
    assert(Math.abs(frequency(results[0])-440)<35);
    assert(Math.abs(frequency(results[1])-880)<35);
    console.log('TWO_WAY_SRTP_AUDIO_OK: door 440Hz -> HomeKit; HomeKit 880Hz -> door');
  }finally{
    clearTimeout(timeout);await manager.stop();
    for(const child of processes)if(child.exitCode===null)child.kill('SIGKILL');
    for(const name of ['home.sdp','door.sdp']){const file=path.join(directory,name);if(fs.existsSync(file))fs.unlinkSync(file);}
    fs.rmdirSync(directory);
  }
})().catch(error=>{console.error(error.message);process.exitCode=1;});
