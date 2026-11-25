'use strict';
// HomeKit Secure Video (HKSV) recording for the BTicino HOMETOUCH doorbell.
//
// When someone rings, the listener decrypts the gateway's H.264 and tees it as
// MPEG-TS to a DEDICATED, HKSV-only loopback port (LIVE_FEED_PORT below, = the
// listener's HKSV_FEED_PORT). This is a separate feed from the shared snapshot /
// Home Assistant port: reading a port nobody else writes or binds means a stuck
// recorder ffmpeg can never contend for the shared feed and take down
// notifications or the live view — the failure mode of the first HKSV attempt,
// which read the shared port. No second call to the gateway, no contention with
// the single call slot, and it works even while HomeKit is viewing live.
//
// HKSV cannot be triggered by the doorbell button itself (an Apple limitation,
// HAP-NodeJS #976): the clip is driven by a MotionSensor the plugin trips on the
// ring. This module only produces the clip; index.js wires the trigger.
//
// The home hub negotiates a fragmented-MP4 configuration (fragment length, video
// codec profile/level/resolution, audio codec). We re-encode the ring video to
// exactly that configuration with ffmpeg and stream the fMP4 boxes back: the
// init segment (ftyp+moov) first, then one moof+mdat fragment at a time.

const { spawn } = require('node:child_process');

const LIVE_FEED_HOST = '127.0.0.1';
// The listener's HKSV_FEED_PORT (LIVE_VIDEO_PORT + 1): a private, HKSV-only copy
// of the ring video, NOT the shared 22300 feed.
const LIVE_FEED_PORT = 22301;
// If no video reaches us this soon after the hub asks, there is nothing to
// record (the ring already ended, or the hub asked on a stale motion event).
const FIRST_FRAGMENT_TIMEOUT_MS = 8000;
// A doorbell clip is short; never let one encoder run away.
const MAX_CLIP_MS = 30000;

const H264_PROFILE = ['baseline', 'main', 'high'];
const H264_LEVEL = ['3.1', '3.2', '4.0'];

// HAP enums differ slightly across homebridge versions; resolve defensively.
function recordingOptions(hap) {
  return {
    prebufferLength: 4000, // declared; our clip starts at the ring, no real pre-roll
    mediaContainerConfiguration: {
      type: hap.MediaContainerType.FRAGMENTED_MP4,
      fragmentLength: 4000,
    },
    video: {
      type: hap.VideoCodecType.H264,
      parameters: {
        profiles: [hap.H264Profile.BASELINE, hap.H264Profile.MAIN, hap.H264Profile.HIGH],
        levels: [hap.H264Level.LEVEL3_1, hap.H264Level.LEVEL3_2, hap.H264Level.LEVEL4_0],
      },
      resolutions: [
        [1280, 720, 30], [1280, 720, 15], [640, 480, 30], [640, 480, 15],
        [640, 360, 30], [320, 240, 30], [320, 240, 15], [320, 180, 30],
      ],
    },
    audio: {
      codecs: [{
        type: hap.AudioRecordingCodecType.AAC_LC,
        audioChannels: 1,
        // HAP's AudioRecordingCodec field is `samplerate` (singular; it accepts
        // an array). Using `samplerates` left it undefined and crashed HAP's
        // TLV encoder at controller construction.
        samplerate: [hap.AudioRecordingSamplerate.KHZ_32],
        bitrateMode: hap.AudioBitrate ? hap.AudioBitrate.VARIABLE : 0,
      }],
    },
  };
}

// Build the ffmpeg command that remuxes the ring feed into the fragmented MP4
// the hub negotiated. A silent AAC track is muxed in so the container matches
// the declared audio codec (video-only clips are rejected by some hubs).
function ffmpegArgs(config, feedUrl) {
  const v = config.videoCodec;
  const res = v.resolution || [1280, 720, 30];
  const [width, height, fps] = res;
  const profile = H264_PROFILE[v.parameters?.profile] || 'main';
  const level = H264_LEVEL[v.parameters?.level] || '4.0';
  const bitrate = Math.max(64, Math.round((v.parameters?.bitRate || 2000)));
  const fragMs = config.mediaContainerConfiguration?.fragmentLength || 4000;
  const fragSec = (fragMs / 1000).toFixed(3);
  const sampleRate = 32000;
  return [
    '-hide_banner', '-loglevel', 'error', '-nostdin',
    '-fflags', '+discardcorrupt+genpts', '-err_detect', 'ignore_err',
    '-f', 'mpegts', '-i', feedUrl,
    '-f', 'lavfi', '-i', `anullsrc=r=${sampleRate}:cl=mono`,
    '-map', '0:v:0', '-map', '1:a:0',
    // Deliver EXACTLY the negotiated WxH: HomeKit silently drops a clip whose
    // real resolution differs from the selectedConfiguration it negotiated. The
    // panel is 400x288, so we must scale up to the negotiated size (fit inside,
    // keep aspect) and pad to the exact frame, not leave it at the source size.
    // setpts=PTS-STARTPTS zero-bases the timestamps: the panel's MPEG-TS starts
    // at a large PTS, which Apple rejects and which also misaligns the forced
    // keyframes below (they are expressed against output time t). Zero-based, the
    // fragments start on an IDR at 0/4/8 s as the hub expects.
    '-vf', `setpts=PTS-STARTPTS,fps=${fps},scale=${width}:${height}:force_original_aspect_ratio=decrease,pad=${width}:${height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p,setsar=1`,
    '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-bf', '0',
    '-profile:v', profile, '-level:v', level,
    '-b:v', `${bitrate}k`, '-maxrate', `${bitrate + 128}k`, '-bufsize', `${2 * bitrate}k`,
    // Every fragment must start on an IDR frame, aligned to the fragment length.
    '-g', String(Math.max(1, Math.round(fps * (fragMs / 1000)))),
    '-force_key_frames', `expr:gte(t,n_forced*${fragSec})`,
    '-c:a', 'aac', '-ar', String(sampleRate), '-ac', '1', '-b:a', '32k',
    '-f', 'mp4',
    '-movflags', 'frag_keyframe+empty_moov+default_base_moof+skip_sidx+skip_trailer',
    '-frag_duration', String(fragMs * 1000),
    'pipe:1',
  ];
}

// Read the MP4 byte stream box by box. Each box is a 32-bit big-endian length
// (including the 8-byte header) followed by a 4-char type and its payload.
class BoxReader {
  constructor(stream) {
    this.iter = stream[Symbol.asyncIterator]();
    this.buf = Buffer.alloc(0);
    this.done = false;
  }
  async _fill(n) {
    while (this.buf.length < n) {
      const { value, done } = await this.iter.next();
      if (done) { this.done = true; return false; }
      this.buf = this.buf.length ? Buffer.concat([this.buf, value]) : value;
    }
    return true;
  }
  async next() {
    if (!await this._fill(8)) return null;
    const length = this.buf.readUInt32BE(0);
    if (length < 8) throw new Error(`MP4 box length non valida: ${length}`);
    if (!await this._fill(length)) return null;
    const type = this.buf.toString('ascii', 4, 8);
    const box = this.buf.subarray(0, length);
    this.buf = this.buf.subarray(length);
    return { type, box };
  }
}

// Yield HKSV RecordingPackets: first the init segment (ftyp+moov), then each
// moof+mdat fragment. One fragment of look-ahead lets us flag the last one.
async function* fragments(stream, signal, log) {
  const reader = new BoxReader(stream);
  const header = [];
  let moof = null;    // the moof awaiting its mdat
  let pending = null;  // a complete packet held back so the final one can be isLast
  while (!signal.aborted) {
    const next = await reader.next();
    if (!next) break;
    if (next.type === 'ftyp' || next.type === 'moov') {
      header.push(next.box);
      if (next.type === 'moov') {
        if (pending) yield { data: pending, isLast: false };
        pending = Buffer.concat(header);
      }
      continue;
    }
    if (next.type === 'moof') { moof = next.box; continue; }
    if (next.type === 'mdat') {
      if (!moof) continue; // mdat without its moof: ignore
      const fragment = Buffer.concat([moof, next.box]);
      moof = null;
      if (pending) yield { data: pending, isLast: false };
      pending = fragment;
    }
    // Any other top-level box (styp, sidx) is intentionally dropped; the muxer
    // flags above already suppress sidx/trailer.
  }
  if (pending && !signal.aborted) yield { data: pending, isLast: true };
  else if (signal.aborted) log?.debug?.('HKSV: generazione interrotta dal controller');
}

class RecordingDelegate {
  constructor({ ffmpeg, log, feedPort = LIVE_FEED_PORT, maxClipMs = MAX_CLIP_MS }) {
    this.ffmpeg = ffmpeg || 'ffmpeg';
    this.log = log;
    this.feedPort = feedPort;
    this.maxClipMs = maxClipMs;
    this.configuration = undefined;
    this.active = false;
    this.sessions = new Map();
  }

  updateRecordingActive(active) {
    this.active = active;
    this.log.info(`HKSV: registrazione ${active ? 'abilitata' : 'disabilitata'} da HomeKit`);
  }

  updateRecordingConfiguration(configuration) {
    this.configuration = configuration;
  }

  async *handleRecordingStreamRequest(streamId) {
    if (!this.configuration) {
      this.log.warn('HKSV: richiesta registrazione senza configurazione negoziata');
      return;
    }
    const controller = new AbortController();
    const feedUrl = `udp://${LIVE_FEED_HOST}:${this.feedPort}?fifo_size=1000000&overrun_nonfatal=1&timeout=${FIRST_FRAGMENT_TIMEOUT_MS * 1000}`;
    const args = ffmpegArgs(this.configuration, feedUrl);
    this.log.info(`HKSV: avvio clip stream=${streamId} dalla suonata (feed ${LIVE_FEED_HOST}:${this.feedPort})`);
    const proc = spawn(this.ffmpeg, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    const session = { proc, controller };
    this.sessions.set(streamId, session);

    let stderr = '';
    proc.stderr.on('data', data => { stderr += data.toString(); });
    proc.on('error', err => { this.log.error(`HKSV: ffmpeg non avviato: ${err.message}`); controller.abort(); });

    // Guards: give up if no fragment arrives, and cap the whole clip.
    let firstFragment = false;
    const firstTimer = setTimeout(() => {
      if (!firstFragment) { this.log.warn('HKSV: nessun video dalla suonata entro il timeout; chiudo'); controller.abort(); }
    }, FIRST_FRAGMENT_TIMEOUT_MS);
    const maxTimer = setTimeout(() => { this.log.info('HKSV: durata massima clip raggiunta'); controller.abort(); }, this.maxClipMs);
    controller.signal.addEventListener('abort', () => {
      clearTimeout(firstTimer); clearTimeout(maxTimer);
      // Once we stop reading ffmpeg's stdout (clip done), it blocks writing the
      // next fragment; then even SIGTERM cannot complete and the process lingers
      // for minutes holding the feed UDP port, so the next ring's clip fails with
      // "bind failed: Address already in use". Drop the pipe to unblock it, then
      // SIGTERM, then SIGKILL as a guaranteed backstop.
      try { proc.stdout.destroy(); } catch (_) { /* ignore */ }
      if (proc.exitCode === null && proc.signalCode === null) {
        try { proc.kill('SIGTERM'); } catch (_) { /* ignore */ }
        setTimeout(() => {
          try { if (proc.exitCode === null && proc.signalCode === null) proc.kill('SIGKILL'); } catch (_) { /* ignore */ }
        }, 2000).unref?.();
      }
    }, { once: true });

    let count = 0;
    try {
      for await (const packet of fragments(proc.stdout, controller.signal, this.log)) {
        if (!firstFragment) { firstFragment = true; clearTimeout(firstTimer); }
        count += 1;
        yield packet;
        if (packet.isLast) break;
      }
      if (count === 0 && stderr.trim()) this.log.warn(`HKSV: ffmpeg senza output: ${stderr.slice(-400).trim()}`);
      this.log.info(`HKSV: clip stream=${streamId} conclusa, frammenti=${count}`);
    } finally {
      clearTimeout(firstTimer); clearTimeout(maxTimer);
      controller.abort();
      this.sessions.delete(streamId);
    }
  }

  acknowledgeStream(streamId) {
    this.closeRecordingStream(streamId, undefined);
  }

  closeRecordingStream(streamId, reason) {
    const session = this.sessions.get(streamId);
    if (!session) return;
    this.sessions.delete(streamId);
    if (reason !== undefined) this.log.info(`HKSV: chiusura stream=${streamId} motivo=${reason}`);
    session.controller.abort();
  }

  closeAll() {
    for (const id of [...this.sessions.keys()]) this.closeRecordingStream(id, undefined);
  }
}

module.exports = { RecordingDelegate, recordingOptions, ffmpegArgs, fragments, BoxReader, LIVE_FEED_PORT };
