'use strict';
// Decode the encrypted output, not just its RTP header. No camera or SIP calls.
const {StreamManager}=require('./stream');
const {spawn}=require('node:child_process');
const dgram=require('node:dgram');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
  const ffmpeg=process.env.BTICINO_TEST_FFMPEG||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
  const socket=dgram.createSocket('udp4');
  await new Promise(r=>socket.bind(0,'127.0.0.1',r));
  const port=socket.address().port;
  await new Promise(r=>socket.close(r));
  const key=Buffer.alloc(16,1),salt=Buffer.alloc(14,2);
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'bticino-srtp-test-'));
  const file=path.join(dir,'input.sdp');
  fs.writeFileSync(file,`v=0\no=- 0 0 IN IP4 127.0.0.1\ns=Test\nc=IN IP4 127.0.0.1\nt=0 0\nm=video ${port} RTP/SAVP 99\na=rtpmap:99 H264/90000\na=fmtp:99 packetization-mode=1\na=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:${Buffer.concat([key,salt]).toString('base64')}\n`,{mode:0o600});
  const decoder=spawn(ffmpeg,['-hide_banner','-loglevel','error','-protocol_whitelist','file,udp,rtp,srtp,crypto','-i',file,'-an','-frames:v','3','-f','framemd5','pipe:1'],{stdio:['ignore','pipe','pipe']});
  let output='',errors='';decoder.stdout.on('data',d=>output+=d);decoder.stderr.on('data',d=>errors+=d);
  const done=new Promise((resolve,reject)=>{decoder.once('error',reject);decoder.once('exit',resolve);});
  const timeout=setTimeout(()=>decoder.kill('SIGKILL'),15000);
  process.env.BTICINO_TEST_PATTERN='1';
  const manager=new StreamManager('unused',console,{ffmpegPath:ffmpeg});
  try {
    await manager.prepare({sessionID:'roundtrip',targetAddress:'127.0.0.1',video:{port,srtpCryptoSuite:0,srtp_key:key,srtp_salt:salt},
      audio:process.env.BTICINO_SYNC_AUDIO === '1' ? {port:port+2,srtpCryptoSuite:0,srtp_key:key,srtp_salt:salt} : undefined});
    await manager.start({sessionID:'roundtrip',video:{width:1280,height:720,fps:30,max_bit_rate:299,pt:99,mtu:1378,profile:2,level:2},
      audio:process.env.BTICINO_SYNC_AUDIO === '1' ? {pt:110,packet_time:20} : undefined});
    const code=await done;
    assert.equal(code,0,errors);
    const frames=output.split('\n').filter(l=>l && !l.startsWith('#'));
    assert.equal(frames.length,3);
    console.log('SRTP_DECRYPT_AND_DECODE_OK frames=3');
  } finally {clearTimeout(timeout);await manager.closeAll();decoder.kill('SIGKILL');fs.unlinkSync(file);fs.rmdirSync(dir);}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
