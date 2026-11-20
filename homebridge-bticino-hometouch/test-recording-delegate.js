'use strict';
// HKSV recording delegate: box parsing, fragment framing and the end-to-end
// clip pump, driven by a fake ffmpeg that emits a crafted fragmented-MP4 stream.
// No real ffmpeg, no network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Readable } = require('node:stream');
const { RecordingDelegate, recordingOptions, ffmpegArgs, fragments, BoxReader } = require('./recording-delegate');

// Build an MP4 box: 4-byte big-endian length (incl. 8-byte header) + 4-char type + payload.
function box(type, payload = Buffer.alloc(0)) {
  const header = Buffer.alloc(8);
  header.writeUInt32BE(8 + payload.length, 0);
  header.write(type, 4, 'ascii');
  return Buffer.concat([header, payload]);
}

const lines = [];
const log = {info: m => lines.push(m), warn: m => lines.push(m), error: m => lines.push(m), debug: () => {}};

const hap = {
  MediaContainerType: {FRAGMENTED_MP4: 0},
  VideoCodecType: {H264: 0},
  H264Profile: {BASELINE: 0, MAIN: 1, HIGH: 2},
  H264Level: {LEVEL3_1: 0, LEVEL3_2: 1, LEVEL4_0: 2},
  AudioRecordingCodecType: {AAC_LC: 0},
  AudioRecordingSamplerate: {KHZ_32: 3},
  AudioBitrate: {VARIABLE: 0},
};

(async () => {
  // 1) recordingOptions: declares fragmented MP4, H.264 and AAC as HKSV requires.
  const opts = recordingOptions(hap);
  assert.equal(opts.mediaContainerConfiguration.type, hap.MediaContainerType.FRAGMENTED_MP4);
  assert.equal(opts.video.type, hap.VideoCodecType.H264);
  assert.equal(opts.audio.codecs[0].type, hap.AudioRecordingCodecType.AAC_LC);
  assert.equal(opts.prebufferLength, 4000);
  assert(opts.video.resolutions.length > 0);

  // 2) ffmpegArgs: maps the negotiated config to profile/level/size and reads the feed.
  const config = {
    mediaContainerConfiguration: {fragmentLength: 4000},
    videoCodec: {resolution: [1280, 720, 24], parameters: {profile: 2, level: 2, bitRate: 800}},
  };
  const args = ffmpegArgs(config, 'udp://127.0.0.1:22300?x=1');
  assert(args.includes('udp://127.0.0.1:22300?x=1'));
  assert.equal(args[args.indexOf('-profile:v') + 1], 'high');
  assert.equal(args[args.indexOf('-level:v') + 1], '4.0');
  assert.equal(args[args.indexOf('-b:v') + 1], '800k');
  assert(args.includes('anullsrc=r=32000:cl=mono'), 'silent AAC track muxed in');
  assert(args[args.indexOf('-movflags') + 1].includes('frag_keyframe'));
  // Defaults when the config omits parameters.
  const bare = ffmpegArgs({videoCodec: {}}, 'udp://x');
  assert.equal(bare[bare.indexOf('-profile:v') + 1], 'main');

  // 3) BoxReader: reassembles boxes even when chunks split a box in half.
  const stream = box('ftyp', Buffer.from('iso5')) ;
  const whole = Buffer.concat([box('ftyp', Buffer.from('iso5')), box('moov', Buffer.from('MOOVDATA'))]);
  const readable = Readable.from([whole.subarray(0, 3), whole.subarray(3, 20), whole.subarray(20)]);
  const reader = new BoxReader(readable);
  const a = await reader.next(); assert.equal(a.type, 'ftyp');
  const b = await reader.next(); assert.equal(b.type, 'moov');
  assert.equal(await reader.next(), null);

  // 4) fragments: init segment first (ftyp+moov together), then moof+mdat pairs,
  //    with isLast only on the final packet.
  const clip = Buffer.concat([
    box('ftyp', Buffer.from('iso5')),
    box('moov', Buffer.from('MOOV')),
    box('moof', Buffer.from('MOOF1')), box('mdat', Buffer.from('DATA1')),
    box('moof', Buffer.from('MOOF2')), box('mdat', Buffer.from('DATA2')),
  ]);
  const controller = new AbortController();
  const out = [];
  for await (const p of fragments(Readable.from([clip]), controller.signal, log)) out.push(p);
  assert.equal(out.length, 3, 'init + two fragments');
  assert.equal(out[0].isLast, false);
  // First packet is the init segment: ftyp followed by moov.
  assert.equal(out[0].data.toString('ascii', 4, 8), 'ftyp');
  assert.equal(out[1].isLast, false);
  assert.equal(out[1].data.toString('ascii', 4, 8), 'moof');
  assert.equal(out[2].isLast, true, 'last fragment flagged isLast');

  // Abort stops the generator promptly.
  const ac2 = new AbortController(); ac2.abort();
  const none = [];
  for await (const p of fragments(Readable.from([clip]), ac2.signal, log)) none.push(p);
  assert.equal(none.length, 0, 'aborted before start yields nothing');

  // 5) End-to-end with a fake ffmpeg that writes the crafted clip to stdout.
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'hksv-test-'));
  const fake = path.join(tmp, 'fake-ffmpeg');
  const b64 = clip.toString('base64');
  fs.writeFileSync(fake, `#!${process.execPath}\nprocess.stdout.write(Buffer.from(${JSON.stringify(b64)}, 'base64'));\nprocess.stdout.end();\n`, {mode: 0o755});

  const delegate = new RecordingDelegate({ffmpeg: fake, log, feedPort: 22300});
  delegate.updateRecordingActive(true);
  // Without a negotiated configuration, a request yields nothing (and warns).
  const before = [];
  for await (const p of delegate.handleRecordingStreamRequest(1)) before.push(p);
  assert.equal(before.length, 0);
  assert(lines.some(l => /senza configurazione/.test(l)));

  delegate.updateRecordingConfiguration(config);
  const got = [];
  for await (const p of delegate.handleRecordingStreamRequest(7)) got.push(p);
  assert.equal(got.length, 3, 'delegate streams init + two fragments from fake ffmpeg');
  assert.equal(got[2].isLast, true);
  assert.equal(delegate.sessions.size, 0, 'session cleaned up after the clip');

  // closeRecordingStream on an unknown id is safe; closeAll clears everything.
  delegate.closeRecordingStream(999, 2);
  delegate.closeAll();

  console.log('RECORDING_DELEGATE_OK (box parsing, framing, fake-ffmpeg clip)');
  fs.rmSync(tmp, {recursive: true, force: true});
  process.exit(0);
})().catch(err => { console.error(err); process.exit(1); });
