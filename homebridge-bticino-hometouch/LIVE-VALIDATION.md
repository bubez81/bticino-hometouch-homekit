# Live validation

## Current status — 2026-10-05

With `standalone: true` the doorbell is advertised as a Video Doorbell
(category 18). Simulated rings produced iPhone/iPad notifications with a scaled
snapshot, and touching and holding the notification showed the locks of the same
room. While bridged, the same rings only chimed HomePods. A real outdoor ring in
standalone mode, sustained live reliability and correct-entrance opening remain
pending. See the [changelog](../CHANGELOG.md#2026-10-05).

## Current status — 2026-10-04

The dedicated bridge runs as a persistent service on the test installation.
Incoming audio/dialog modules are integrated, and opening incoming live can
accept the call with the microphone still muted. Talk enables outgoing audio.
The separate contextual lock is implemented with assumed pulse state; physical
selection and opening still require validation.

Dedicated-account migration completed with successful SIP registration. A
HomeKit video session returned receiver reports after migration. Its audio was
explicitly diagnostic silence, so it did not validate the outdoor microphone.
Do not infer fresh live imagery from encoded-frame or reception counters alone.

Local checks: 86 Python tests (one optional dependency skip), plugin lifecycle
and lock tests, encrypted video decode and bidirectional synthetic audio passed.
The old camera-probe fixture failure described below has been corrected.
Real ring notification, conversation and opening after migration remain pending.

## Historical development log — 2026-09-16

The following entries preserve intermediate observations. Statements about
foreground execution, missing integration and outstanding fixture failures
describe that date, not the current implementation above.

Only the isolated Cubetto test bridge was changed (`/private/tmp/bticino-child`, port 51991).
The main Homebridge was not changed. No HomeKit identity reset is necessary.

## Observations

- Real-source 1280x720 sessions repeatedly ended with a HomeKit STOP after 30 seconds despite encoded frames.
- Authenticated SRTCP receiver reports confirmed advancing received sequence numbers and zero reported loss, with repeated PLI requests.
- Combining silent audio and video in one FFmpeg process did not fix this. `BTICINO_SYNC_AUDIO` is an opt-in diagnostic, OFF in the current deployment.
- Added `fps=<negotiated>,realtime` after resetting PTS, before scaling with square pixel aspect ratio.
- At 18:35:10 the next session negotiated 640x360 through a different receiver address. Casa showed “In tempo reale”; the session exceeded 60 seconds and 1700 frames without repeated PLI. This is encouraging but does NOT isolate the pacing change from the changed receiver/resolution.
- Audio remains synthetic silence, NOT the doorbell microphone; bidirectional audio is not implemented.

## Checks and remaining work

`node test-srtp-roundtrip.js` independently decrypts and decodes three synthetic frames; passed after the pacing change. It does not prove real-source reliability.

Check longer duration, repeat openings and direct 1280x720 playback before declaring resolution. The test bridge still runs in a foreground SSH session. The probe and StreamManager impose a 300-second duration. The probe's media loop does not process inbound SIP requests. IPC terminates the probe after two seconds although graceful SIP termination can need five seconds. Those lifecycle issues remain uncorrected.

## Later results / deployed changes

- The apparently successful 18:35 stream stopped advancing at 1703 frames. Do not describe it as sustained working live.
- Isolated source test with authenticated SRTCP feedback received 1534 packets and an explicit remote BYE around 60 seconds. Another accepted test received zero RTP packets and also ended remotely. Feedback did not eliminate the source termination.
- IPC graceful termination timeout was increased to eight seconds, preserving the installed module's other contents. Backup: `/opt/bticino-sniffer/backups/ipc-cleanup-it1iite0`.
- Updated probe now reads signaling during media reception and acknowledges remote BYE. It is deployed with the old probe backed up in the directory above. RTCP feedback is opt-in and is not enabled by the installed IPC invocation.
- StreamManager polls source status and reopens an ended source without replacing its HomeKit encoder or SRTP context, limited to three consecutive unsuccessful reopenings. Unit transport test verifies renewal retains the encoder and SSRC.
- Current test bridge uses `BTICINO_MAX_WIDTH=640` to compare the lower resolution. Direct Mac playback has NOT yet been verified working; recent attempts received no source frames.
- Audio remains silence; synchronized-audio experiment is OFF. Local index.js callback fix has not been deployed.
- Awaiting comparison with live viewing in official Door Entry app; do not imply the project is finished.

### Historical stage: audio modules before integration

User now explicitly requests a full HomeKit doorbell, including real two-way audio and answering an incoming call without creating a second outbound call.

New local components:

- `two-way-audio.js`: two independent encrypted audio conversion paths (Opus/HomeKit and PCMU or PCMA/door), private temporary SDPs, mono, bounded cleanup. No actual microphone is used by tests.
- `test-two-way-audio.js`: independent encrypted generators and decoders verify 440-Hz audio reaches the HomeKit side and 880-Hz audio reaches the door side. This is a transport test, NOT hardware verification.
- `src/bticino_audio_offer.py`: codec selection, direction/port/address/key validation; rejects unsupported encryption and codecs.
- `src/bticino_incoming_dialog.py`: isolated incoming SIP dialog state machine with ready-audio gate, ownership, 200/ACK/CANCEL/BYE and duplicate handling.

These modules are intentionally NOT imported by the deployed listener/plugin yet. Remaining integration: attach the active incoming call's encrypted media to loopback relays, expose controlled IPC attach/answer/hangup operations, wire HAP twoWayAudio negotiation and return audio, and ensure early SDP/final answer directions agree. Do not advertise working two-way audio until connected and tested on the physical doorbell. Existing deployed audio is still synthetic silence.

`rtcp-diagnostic.js` authenticates SRTCP before parsing and logs only reception statistics, never keys or media payloads.
### Historical stage: incoming-call audio integration — 2026-09-16 19:39

Installed on Cubetto's **test bridge only** (port 51991) and its SIP listener.
Backup: `/opt/bticino-sniffer/backups/incoming-audio-ltrgagu6` (numbered files with manifest).
Bridge execution session: 66723. SIP re-registration returned 200; IPC ping and
incoming_status succeeded after restart. Homebridge loaded without errors.

`enableTwoWayAudio: true` in test accessory config; `incoming_audio: true` in
listener config. Existing incoming call attaches encrypted video/audio through
private loopback relays. OPUS ↔ PCMU/PCMA conversion uses private SDP files.
Speaker mute is set at each preparation. Speaker unmute requests SIP acceptance;
muting blocks return audio, closing releases the attachment and sends BYE if answered.
Opening a view alone does not accept the call. Outgoing on-demand calls still use
the existing video-only probe and diagnostic silent audio, **not real two-way audio**.

Passed: incoming listener/dialog, IPC, audio offer and loopback attachment tests;
Node incoming adapter lifecycle, bidirectional encrypted audio and existing video
transport/renewal tests. Full Python suite still has a camera-probe fixture failure
(`Missing video` in the mock 200 response) unrelated to the new incoming tests.

**Not yet verified on hardware:** external microphone audio, Home app Parla event,
SIP 200/ACK from gateway after answer, external speaker playback and end-to-end latency.
Needs a real ring and a person at the door. Do not describe this as fully validated.
No GitHub publication or production Homebridge changes.
