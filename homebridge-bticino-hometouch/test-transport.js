'use strict';
// Standalone loopback transport test. Never connects to the production IPC.
const assert = require('node:assert/strict');
const dgram = require('node:dgram');
const {spawn} = require('node:child_process');
const ipc = require('./ipc');
const {StreamManager} = require('./stream');
const ffmpeg = process.env.BTICINO_TEST_FFMPEG || '/opt/homebrew/opt/ffmpeg/bin/ffmpeg';
(async () => {
  const sink = dgram.createSocket('udp4');
  await new Promise(resolve => sink.bind(0, '127.0.0.1', resolve));
  let generator, stops = 0, starts = 0;
  let sourceEnded = false;
  let renewalDone;
  const renewed = new Promise(resolve => { renewalDone = resolve; });
  ipc.request = async (_path, command, payload) => {
    if (command === 'start_call') {
      starts++;
      generator?.kill('SIGTERM');
      assert.equal(payload.session_id, 'transport-test');
      generator = spawn(ffmpeg, ['-hide_banner','-loglevel','error','-re','-f','lavfi','-i',
        'testsrc2=size=640x480:rate=30','-an','-c:v','libx264','-preset','ultrafast',
        '-tune','zerolatency','-g','30','-f','mpegts',`udp://127.0.0.1:${payload.video_port}?pkt_size=1316`],
        {stdio:'ignore'});
      if (starts === 2) renewalDone();
      return {ok:true};
    }
    if (command === 'status') return {ok:true,state:sourceEnded && starts===1 ? 'idle' : 'calling'};
    assert.equal(command,'stop_call'); stops++;
    generator?.kill('SIGTERM');
    return {ok:true};
  };
  const manager = new StreamManager('unused', console, {ffmpegPath:ffmpeg});
  try {
    const response = await manager.prepare({sessionID:'transport-test',targetAddress:'127.0.0.1',
      video:{port:sink.address().port,srtpCryptoSuite:0,srtp_key:Buffer.alloc(16,1),srtp_salt:Buffer.alloc(14,2)}});
    const received = new Promise((resolve,reject) => {
      const timer=setTimeout(()=>reject(Error('No SRTP packets after 20 seconds')),20000);
      sink.on('message',packet=>{
        if(packet.length>22 && (packet[1]&127)===99){
          clearTimeout(timer);resolve(packet);
        }
      });
    });
    await manager.handle({sessionID:'transport-test',type:'start',
      video:{width:640,height:480,fps:30,max_bit_rate:300,pt:99,mtu:1200,profile:0,level:0}});
    const packet=await received;
    assert.equal(packet.readUInt32BE(8),response.video.ssrc);
    sourceEnded = true;
    const encoder = manager.sessions.get('transport-test').process;
    let renewalTimer;
    try {
      await Promise.race([renewed, new Promise((_,reject)=>{renewalTimer=setTimeout(()=>reject(Error('Source was not reopened')),5000);})]);
    } finally { clearTimeout(renewalTimer); }
    assert.equal(starts,2);
    assert.equal(manager.sessions.get('transport-test').process,encoder);
    assert.equal(manager.sessions.get('transport-test').ssrc,response.video.ssrc);
    await manager.handle({sessionID:'transport-test',type:'stop'});
    assert.equal(manager.sessions.size,0);assert.equal(stops,1);
    console.log('LOOPBACK_SRTP_PACKETS_AND_SESSION_CLEANUP_OK');
  } finally {await manager.closeAll();generator?.kill('SIGTERM');sink.close();}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
