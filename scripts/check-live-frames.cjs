'use strict';
const {request}=require('/private/tmp/bticino-child/plugin/ipc');
const {spawn}=require('node:child_process');
const dgram=require('node:dgram');
const crypto=require('node:crypto');
(async()=>{
 const socket=dgram.createSocket('udp4');
 await new Promise(r=>socket.bind(0,'127.0.0.1',r));
 const port=socket.address().port;await new Promise(r=>socket.close(r));
 const id=crypto.randomUUID();let frames=[];
 const child=spawn('/opt/homebrew/opt/ffmpeg/bin/ffmpeg',['-hide_banner','-loglevel','error','-nostdin','-f','mpegts','-i',`udp://127.0.0.1:${port}?fifo_size=1024&overrun_nonfatal=1`,'-an','-vf','fps=1','-f','framemd5','pipe:1'],{stdio:['ignore','pipe','ignore']});
 let text='';child.stdout.on('data',d=>{text+=d;});
 try {
   const result=await request('/tmp/bticino-hometouch.sock','start_call',{session_id:id,candidate:'1',video_port:port},5000);
   if(!result.ok)throw Error(result.error);
   console.log('ISOLATED_SIP_CAPTURE_STARTED');
   await new Promise(r=>setTimeout(r,20000));
 } finally {
   await request('/tmp/bticino-hometouch.sock','stop_call',{session_id:id},5000).catch(()=>{});
   child.kill('SIGTERM');
   const kill=setTimeout(()=>child.kill('SIGKILL'),1500);
   await new Promise(r=>child.once('exit',r));clearTimeout(kill);
   frames=text.split('\n').filter(l=>l.trim()&&!l.startsWith('#'));
   console.log(JSON.stringify({decodedSamples:frames.length,distinctFrames:new Set(frames.map(l=>l.split(',').pop().trim())).size}));
 }
})().catch(e=>{console.error(e.message);process.exitCode=1;});
