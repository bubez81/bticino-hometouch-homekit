'use strict';
// Exercises the real adapter/encoders against loopback, never the live IPC.
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const dgram=require('node:dgram');
const fs=require('node:fs');
const ipc=require('./ipc');
const {IncomingCall,pair}=require('./incoming-call');
const ffmpeg=process.env.BTICINO_TEST_FFMPEG||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
(async()=>{
  const output=await pair(), remote=await pair();
  const socket=dgram.createSocket('udp4');
  await new Promise(resolve=>socket.bind(0,'127.0.0.1',resolve));
  const calls=[];
  const key=crypto.randomBytes(30).toString('base64');
  ipc.request=async (_path,command,payload)=>{
    calls.push({command,...payload});
    assert.equal(payload.session_id,'incoming-test');
    if(command==='attach_incoming')return {ok:true,video:{payload:96,fmtp:'',material:key},
      audio:{codec:'SPEEX',payload:97,material:key,return_material:key,return_port:remote.port},pcm:true,talk:true};
    assert(['answer_incoming','release_incoming'].includes(command));return {ok:true};
  };
  const session={id:'incoming-test',audioSocket:socket,audioSsrc:123,req:{targetAddress:'127.0.0.1',
    audio:{port:output.port,srtp_key:crypto.randomBytes(16),srtp_salt:crypto.randomBytes(14)}}};
  const adapter=new IncomingCall({socketPath:'unused',config:{ffmpegPath:ffmpeg},log:console},session,
    {audio:{codec:'OPUS',channel:1,sample_rate:24,pt:110,packet_time:20}});
  try {
    await adapter.start();
    assert.equal(fs.statSync(adapter.videoPath).mode&0o777,0o600);
    assert.equal(calls.length,1); // preparation alone must not answer
    assert(Number.isInteger(calls[0].pcm_port)&&calls[0].pcm_port===adapter.audio.panelPort);
    // The microphone reaches the door only while talking is enabled.
    const peer={address:'127.0.0.1',port:output.port};
    const packet=Buffer.alloc(40,1);packet[0]=0x80;packet[1]=110;
    adapter.receive(packet,peer);
    assert.equal(adapter.audio.talkPackets||0,0);
    await adapter.answerMuted();
    assert.equal(calls[1].answer,true);
    assert.equal(calls[1].enabled,false);
    assert.equal(adapter.talkEnabled,false);
    await adapter.talk(true);
    adapter.receive(packet,peer);
    assert.equal(adapter.audio.talkPackets,1);
    await adapter.talk(false);
    assert.deepEqual(calls.slice(1).map(c=>c.enabled),[false,true,false]);
    const directory=adapter.directory;
    await Promise.all([adapter.close(),adapter.close()]);
    assert.equal(calls.filter(c=>c.command==='release_incoming').length,1);
    assert(!fs.existsSync(directory));
    console.log('INCOMING_ADAPTER_PREPARE_TALK_MUTE_RELEASE_OK');
  } finally {await adapter.close();output.close();remote.close();socket.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
