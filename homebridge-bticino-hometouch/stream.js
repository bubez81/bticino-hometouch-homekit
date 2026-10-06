'use strict';
const crypto = require('node:crypto');
const dgram = require('node:dgram');
const net = require('node:net');
const {spawn} = require('node:child_process');
const ipc = require('./ipc');
const {IncomingCall}=require('./incoming-call');
const {LiveAudio,TALK_PORT}=require('./live-audio');
const {randomSsrc}=require('./two-way-audio');
class StreamManager {
  constructor(socketPath, log, config = {}) {
    Object.assign(this, {socketPath, log, config});
    this.sessions = new Map();
  }
  async prepare(req) {
    if (!req.sessionID || !net.isIP(req.targetAddress) || req.video.srtpCryptoSuite !== 0 ||
        req.video.srtp_key.length !== 16 || req.video.srtp_salt.length !== 14) throw Error('Invalid SRTP parameters');
    if (this.sessions.size) throw Error('Camera busy');
    const socket = dgram.createSocket(net.isIP(req.targetAddress) === 6 ? 'udp6' : 'udp4');
    const audioSocket = dgram.createSocket(net.isIP(req.targetAddress) === 6 ? 'udp6' : 'udp4');
    const s = {id:req.sessionID, req, socket, audioSocket, audioSsrc:randomSsrc(), ssrc:randomSsrc(), state:'preparing'};
    this.sessions.set(s.id, s);
    try {
      await new Promise((resolve,reject) => {socket.once('error',reject); socket.bind(0,resolve);});
      await new Promise((resolve,reject) => {audioSocket.once('error',reject); audioSocket.bind(0,resolve);});
      socket.on('error', () => this.stop(s.id).catch(()=>{}));
      s.forwarder = dgram.createSocket('udp4');
      s.rtcpForwarder = dgram.createSocket('udp4');
      await new Promise((resolve,reject) => {s.forwarder.once('error',reject);s.forwarder.bind(0,'127.0.0.1',resolve);});
      await new Promise((resolve,reject) => {s.rtcpForwarder.once('error',reject);s.rtcpForwarder.bind(s.forwarder.address().port + 1,'127.0.0.1',resolve);});
      s.forwarder.on('error', () => this.stop(s.id).catch(()=>{}));
      const forwardPacket = (packet, peer) => {
        // The encoder may still flush packets while the session closes its sockets.
        if (s.state === 'stopping' || peer.address !== '127.0.0.1' || packet.length < 8) return;
        const rtcp = packet[1] >= 192 && packet[1] <= 223;
        if (!rtcp && packet.length < 12) return;
        if (rtcp) s.encoderRtcpPort = peer.port;
        else {
          s.encoderRtpPort = peer.port;
          if (!s.sentRtp) this.log.info(`HomeKit first RTP: pt=${packet[1]&127} ssrc=${packet.readUInt32BE(8)} destination=${req.targetAddress}:${req.video.port}`);
          s.sentRtp = (s.sentRtp || 0) + 1;
          s.reopenAttempts = 0;
          if (packet[1] & 128) {
            const timestamp=packet.readUInt32BE(4);
            s.markers=(s.markers||0)+1;
            if(s.markers<=5)this.log.info(`HomeKit RTP timing: frame=${s.markers} delta=${s.lastTimestamp===undefined ? 0 : (timestamp-s.lastTimestamp)>>>0}`);
            s.lastTimestamp=timestamp;
          }
        }
        socket.send(packet, req.video.port, req.targetAddress, error => {
          if (error) {this.log.error(`HomeKit UDP send failed: ${error.code}`);this.stop(s.id).catch(()=>{});}
        });
      };
      s.forwarder.on('message', forwardPacket);
      s.rtcpForwarder.on('message', forwardPacket);
      socket.on('message', packet => {
        s.receivedPackets = (s.receivedPackets || 0) + 1;
        if (s.receivedPackets === 1) this.log.info(`HomeKit first video feedback: bytes=${packet.length}`);
        if (s.receivedPackets % 10 === 1) this.log.info(`HomeKit receiver report: ${JSON.stringify(require('./rtcp-diagnostic').inspect(packet,req.video.srtp_key,req.video.srtp_salt))}`);
        const port = s.encoderRtcpPort || (s.encoderRtpPort && s.encoderRtpPort + 1);
        if (port && s.state !== 'stopping') s.forwarder.send(packet,port,'127.0.0.1',()=>{});
      });
      s.state = 'prepared';
      s.timer = setTimeout(()=>this.stop(s.id).catch(()=>{}),30000);
      const response = {video:{port:socket.address().port,ssrc:s.ssrc,srtp_key:req.video.srtp_key,srtp_salt:req.video.srtp_salt}};
      if (req.audio && Buffer.isBuffer(req.audio.srtp_key) && Buffer.isBuffer(req.audio.srtp_salt)) {
        s.audioSocket = audioSocket;
        response.audio = {port:audioSocket.address().port,ssrc:s.audioSsrc,srtp_key:req.audio.srtp_key,srtp_salt:req.audio.srtp_salt};
        audioSocket.on('error', () => this.stop(s.id).catch(()=>{}));
        audioSocket.on('message', (packet, peer) => {
          if(s.incoming){s.incoming.receive(packet,peer);return;}
          if (s.liveAudio && peer.address === req.targetAddress && peer.port === req.audio.port) {
            // iPhone microphone: passed to the door only while Home's speaker is unmuted.
            if (this.talkEnabled === true) s.liveAudio.receiveTalk(packet);
            return;
          }
          if (peer.address !== '127.0.0.1' || s.state === 'stopping') return;
          audioSocket.send(packet, req.audio.port, req.targetAddress, error => {
            if (error) this.log.error(`HomeKit audio UDP: ${error.code}`);
          });
        });
      } else { try { audioSocket.close(); } catch (_) {} }
      return response;
    } catch(e) {await this.stop(s.id); throw e;}
  }
  async start(req) {
    const s = this.sessions.get(req.sessionID);
    if (!s || s.state !== 'prepared') throw Error('Session not prepared');
    s.state = 'starting'; clearTimeout(s.timer);
    try {
      const reserve = dgram.createSocket('udp4');
      await new Promise((resolve,reject)=>{reserve.once('error',reject);reserve.bind(0,'127.0.0.1',resolve);});
      const port = reserve.address().port;
      await new Promise(resolve=>reserve.close(resolve));
      const v=req.video;
      if(this.config.enableTwoWayAudio===true) {
        const incoming=await ipc.request(this.socketPath,'incoming_status');
        if(incoming.incoming) {
          s.incoming=new IncomingCall(this,s,req);
          await s.incoming.start();
        }
      }
      if (req.audio) this.log.info(`HomeKit audio request: ${JSON.stringify(req.audio)}`);
      for (const n of [v.width,v.height,v.fps,v.max_bit_rate,v.pt,v.mtu,s.req.video.port]) {
        if (!Number.isInteger(n)||n<1||n>65535) throw Error('Invalid video parameters');
      }
      this.log.info(`HomeKit negotiated video ${v.width}x${v.height} fps=${v.fps} bitrate=${v.max_bit_rate} pt=${v.pt} profile=${v.profile} level=${v.level}`);
      const outputWidth = process.env.BTICINO_MAX_WIDTH === '640' ? Math.min(v.width,640) : v.width;
      const outputHeight = Math.max(2,Math.round(v.height*outputWidth/v.width/2)*2);
      this.log.info(`HomeKit encoded resolution ${outputWidth}x${outputHeight}`);
      const args=['-hide_banner','-loglevel','error','-nostdin','-probesize','32768','-analyzeduration','0','-use_wallclock_as_timestamps','1','-f','mpegts',
        '-i',`udp://127.0.0.1:${port}?fifo_size=1024&overrun_nonfatal=1`,
        '-an','-map','0:v:0','-vf',`setpts=PTS-STARTPTS,fps=${v.fps},realtime,scale=${outputWidth}:${outputHeight},setsar=1`,'-r',String(v.fps),'-c:v','libx264','-preset','ultrafast',
        '-tune','zerolatency','-pix_fmt','yuv420p','-profile:v',['baseline','main','high'][v.profile]||'baseline',
        '-level:v',['3.1','3.2','4.0'][v.level]||'3.1','-b:v',`${v.max_bit_rate}k`,'-g',String(v.fps*2),
        '-payload_type',String(v.pt),'-ssrc',String(s.ssrc),'-f','rtp','-srtp_out_suite','AES_CM_128_HMAC_SHA1_80',
        '-srtp_out_params',Buffer.concat([Buffer.from(s.req.video.srtp_key||[]),Buffer.from(s.req.video.srtp_salt||[])]).toString('base64'),
        `srtp://127.0.0.1:${s.forwarder.address().port}?rtcpport=${s.rtcpForwarder.address().port}&pkt_size=${Math.min(v.mtu,1316)}`];
      args.unshift('-progress', 'pipe:1', '-stats_period', '1');
      if(s.incoming) {
        const start=args.indexOf('-probesize'),end=args.indexOf('-an');
        args.splice(start,end-start,'-protocol_whitelist','file,udp,rtp,srtp,crypto','-probesize','32768','-analyzeduration','0','-i',s.incoming.videoPath);
      }
      const combinedAudio = !s.incoming && process.env.BTICINO_SYNC_AUDIO === '1' && req.audio && s.req.audio?.srtp_key && s.req.audio?.srtp_salt;
      const diagnostic = !s.incoming && process.env.BTICINO_TEST_PATTERN === '1';
      if (!s.incoming && !diagnostic && !combinedAudio && this.config.liveAudio !== false && req.audio &&
          s.audioSocket && s.req.audio?.srtp_key && s.req.audio?.srtp_salt) {
        // The gateway sends the panel's audio only on a Speex call that also
        // receives client audio; the camera call handles both, this relays them.
        s.liveAudio = new LiveAudio({ffmpeg:this.config.audioFfmpegPath||this.config.ffmpegPath||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg',
          log:this.log, key:s.req.audio.srtp_key, salt:s.req.audio.srtp_salt, pt:req.audio.pt, ssrc:s.audioSsrc,
          packetTime:req.audio.packet_time, homekitPort:s.audioSocket.address().port, talkPort:this.config.talkPort||TALK_PORT});
        await s.liveAudio.start();
        this.log.info('HomeKit live audio: entrance panel sound, microphone when Home unmutes');
      }
      if (!s.incoming && !combinedAudio && !s.liveAudio && s.audioSocket && s.req.audio?.srtp_key && s.req.audio?.srtp_salt) {
        s.audioProcess = spawn(this.config.ffmpegPath||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg', ['-hide_banner','-loglevel','error','-nostdin','-re','-f','lavfi','-i','anullsrc=r=24000:cl=mono','-c:a','libopus','-ar','24000','-ac','1','-b:a','24k','-application','lowdelay','-payload_type',String(req.audio.pt),'-ssrc',String(s.audioSsrc),'-f','rtp','-srtp_out_suite','AES_CM_128_HMAC_SHA1_80','-srtp_out_params',Buffer.concat([s.req.audio.srtp_key,s.req.audio.srtp_salt]).toString('base64'),`srtp://127.0.0.1:${s.audioSocket.address().port}?rtcpport=${s.audioSocket.address().port}`],{stdio:'ignore'});
        s.audioProcess.on('error', error => this.log.error(`HomeKit audio encoder: ${error.message}`));
        this.log.info('HomeKit diagnostic audio: silence only, not doorbell microphone');
      }
      if (process.env.BTICINO_DIRECT_VIDEO === '1') {
        const host = net.isIP(s.req.targetAddress) === 6 ? `[${s.req.targetAddress}]` : s.req.targetAddress;
        args[args.length - 1] = `srtp://${host}:${s.req.video.port}?rtcpport=${s.req.video.port}&pkt_size=${Math.min(v.mtu, 1200)}`;
        this.log.info('HomeKit diagnostic: direct SRTP video transport');
      }
      if (diagnostic) {
        const start = args.indexOf('-probesize');
        const end = args.indexOf('-an');
        args.splice(start,end-start,'-re','-f','lavfi','-i',`testsrc2=size=${v.width}x${v.height}:rate=${v.fps}`);
        this.log.warn('DIAGNOSTIC: animated test pattern; not the BTicino camera');
      }
      if (combinedAudio) {
        // Both outputs share one encoder clock; the diagnostic audio is silence.
        const outputStart = args.indexOf('-an');
        args.splice(outputStart, 0, '-re', '-f', 'lavfi', '-i', 'anullsrc=r=24000:cl=mono');
        args.push('-map', '1:a:0', '-vn', '-c:a', 'libopus', '-ar', '24000', '-ac', '1',
          '-b:a', '24k', '-application', 'lowdelay', '-frame_duration', String(req.audio.packet_time || 20),
          '-payload_type', String(req.audio.pt), '-ssrc', String(s.audioSsrc), '-f', 'rtp',
          '-srtp_out_suite', 'AES_CM_128_HMAC_SHA1_80', '-srtp_out_params',
          Buffer.concat([s.req.audio.srtp_key, s.req.audio.srtp_salt]).toString('base64'),
          `srtp://127.0.0.1:${s.audioSocket.address().port}?rtcpport=${s.audioSocket.address().port}`);
        this.log.info('HomeKit diagnostic: synchronized video and silent audio');
      }
      s.process=spawn(this.config.ffmpegPath||'/opt/homebrew/opt/ffmpeg/bin/ffmpeg',args,{stdio:['ignore','pipe','pipe']});
      s.process.stderr.on('data', data => {
        const message = data.toString().replace(/(srtp_out_params\s+)\S+/g, '$1[redacted]');
        this.log.error(`HomeKit video encoder: ${message.slice(0, 1500).trim()}`);
      });
      let progress = '';
      s.process.stdout.on('data', data => {
        progress += data.toString();
        const lines = progress.split('\n'); progress = lines.pop();
        for (const line of lines) if (/^frame=\d+$/.test(line)) {
          const frames = Number(line.slice(6));
          if (frames > 0 && (!s.frames || frames - s.frames >= 100)) {
            s.frames = frames; this.log.info(`HomeKit encoded frames=${frames}`);
          }
        }
      });
      await new Promise((resolve,reject)=>{s.process.once('spawn',resolve);s.process.once('error',reject);});
      s.process.once('exit',(code,signal)=>{this.log.info(`HomeKit encoder closed code=${code} signal=${signal} frames=${s.frames||0}`);this.stop(s.id).catch(()=>{});});
      if (!diagnostic && !s.incoming) s.pending=ipc.request(this.socketPath,'start_call',{candidate:this.config.candidate||'1',session_id:s.id,video_port:port,
        ...(s.liveAudio ? {audio:true,audio_port:s.liveAudio.panelPort} : {})},5000);
      const result=diagnostic || s.incoming ? {ok:true} : await s.pending;
      if (!result.ok) throw Error(result.error||'SIP start failed');
      if(s.state==='stopping')return;
      s.state='streaming';
      if(s.incoming) {
        // Opening a live incoming call accepts SIP, but never opens the microphone.
        await s.incoming.answerMuted();
        if(this.talkEnabled===true)await s.incoming.talk(true);
      }
      if (!diagnostic && !s.incoming) {
        // The gateway can end its SIP dialog while HomeKit is still viewing.
        // Keep the same HomeKit encoder/keys/SSRC and renew only the source.
        s.sourceWatch = setInterval(async () => {
          if (s.sourceChecking || s.state !== 'streaming') return;
          s.sourceChecking = true;
          try {
            const status = await ipc.request(this.socketPath, 'status', {}, 2000);
            if (s.state !== 'streaming' || status.state !== 'idle') return;
            s.reopenAttempts = (s.reopenAttempts || 0) + 1;
            if (s.reopenAttempts > 3) {
              this.log.error('HomeKit source unavailable after three reopen attempts');
              await this.stop(s.id);
              return;
            }
            this.log.info('HomeKit source ended: reopening SIP while preserving video session');
            s.pending = ipc.request(this.socketPath, 'start_call', {candidate:this.config.candidate||'1',session_id:s.id,video_port:port,
        ...(s.liveAudio ? {audio:true,audio_port:s.liveAudio.panelPort} : {})},5000);
            const renewed = await s.pending;
            if (!renewed.ok) throw Error(renewed.error || 'SIP source renewal failed');
          } catch (error) {
            this.log.error(`HomeKit source renewal: ${error.message}`);
          } finally { s.sourceChecking = false; }
        }, 1500);
      }
      s.timer=setTimeout(()=>this.stop(s.id).catch(()=>{}),300000);
      this.log.info('HomeKit video transport started');
    } catch(e) {await this.stop(s.id);throw e;}
  }
  async handle(req) {
    this.log.info(`HomeKit session request: ${req.type}`);
    if(req.type==='stop')return this.stop(req.sessionID);
    if(req.type==='start')return this.start(req);
    if(req.type==='reconfigure') {
      // HAP reconfigure requests intentionally omit SRTP keys and ports;
      // keep the active transport and acknowledge the renegotiation.
      return;
    }
    throw Error('Reconfiguration requires a new stream');
  }
  async setTalkEnabled(enabled) {
    this.talkEnabled=enabled;
    for(const s of this.sessions.values())if(s.incoming)await s.incoming.talk(enabled);
  }
  async stop(id) {
    const s=this.sessions.get(id);
    if(!s||s.state==='stopping')return;
    s.state='stopping';clearTimeout(s.timer);
    clearInterval(s.sourceWatch);
    await s.incoming?.close();
    this.log.info(`HomeKit session closing: sent=${s.sentRtp||0} feedback=${s.receivedPackets||0} frames=${s.frames||0}`);
    const terminated = s.process && s.process.exitCode === null && s.process.signalCode === null
      ? new Promise(resolve => {
        const timer = setTimeout(() => s.process.kill('SIGKILL'), 2000);
        s.process.once('exit', () => { clearTimeout(timer); resolve(); });
        s.process.kill('SIGTERM');
      }) : Promise.resolve();
    try{s.socket.close();}catch(_){}
    try{s.audioSocket?.close();}catch(_){}
    try{s.audioProcess?.kill('SIGTERM');}catch(_){}
    try{await s.liveAudio?.stop();}catch(_){}
    try{s.forwarder?.close();}catch(_){}
    try{s.rtcpForwarder?.close();}catch(_){}
    try{if(s.pending){await s.pending.catch(()=>{});await ipc.request(this.socketPath,'stop_call',{session_id:id},12000);}}
    finally{await terminated;this.sessions.delete(id);}
  }
  async closeAll(){await Promise.all([...this.sessions.keys()].map(id=>this.stop(id)));}
}
module.exports={StreamManager};
