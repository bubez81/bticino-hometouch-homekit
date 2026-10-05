# Standalone Homebridge plugin

Setup guide: [homebridge.md](homebridge.md). This page describes the design.

The `homebridge-bticino-hometouch/` package is intentionally separate from
`homebridge-camera-ffmpeg`. It can run in an isolated Homebridge instance. The
Python listener owns SIP signaling; the plugin manages HomeKit media sessions
through a local Unix socket. Current status: experimental, 2026-10-04.

Implemented components:

1. Local IPC connecting the plugin to the Python SIP listener.
2. Doorbell/camera accessory, encrypted video transport and source renewal.
3. Incoming-call attachment, explicit answer/hangup and gated two-way audio.
4. Optional contextual lock bound to the camera's owned incoming session.

A general-purpose dedicated-plugin installer remains pending. The existing
Homebridge configuration helper targets Camera-ffmpeg; historical deployment
scripts contain installation-specific paths. No npm publication is included.

## Configuration

`config.child.example.json` describes an isolated test instance. Choose your own
bridge identity and pairing code for a new installation; preserve them on updates.
Enable listener `BTICINO_IPC_ENABLED=1`, give the Homebridge service user access
to its Unix socket, and set the camera's `ipcSocket` to that path.

Set `standalone: true` (recommended since 2026-10-05). The doorbell is then
published as a separate HomeKit accessory with the Video Doorbell category,
paired with the bridge's code. Only that mode delivered ring notifications on the
test installation. See the [changelog](../CHANGELOG.md#2026-10-05) for pairing
steps and Home settings. Snapshots are scaled to the requested size with
`ffmpegPath` and logged with request and result.

`enableCamera` and `enableHapLive` control experimental camera/live behavior.
On-demand viewing requires locally discovered camera candidates and FFmpeg.
Incoming audio requires listener `incoming_audio` and camera `enableTwoWayAudio`.
Opening incoming live may accept the call; talk enables outgoing microphone audio.
Outgoing preview audio is currently diagnostic silence.

Contextual opening requires listener `incoming_unlock` and camera
`enableCallUnlock`. For a separate lock, set camera `separateCallUnlock: true`
and add a `BTicinoCallLock` accessory with matching `cameraName` and `ipcSocket`.
Assign it to the camera's room in Apple Home. See
[contextual opening](contextual-unlock.md) for guards and assumed display state.

## Validation

Run `npm test` inside the plugin directory for lifecycle and lock checks.
`BTICINO_TEST_FFMPEG=/path/to/ffmpeg npm run test:media` checks synthetic encrypted
audio/video over loopback. It does not ring or open a physical door. See
[current validation](../homebridge-bticino-hometouch/LIVE-VALIDATION.md).

No credentials, certificates, snapshots or gateway identifiers belong in this
package or in the Git repository.
