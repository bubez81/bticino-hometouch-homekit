# Homebridge (Apple Home)

This guide connects the listener to Apple Home through a dedicated Homebridge
instance running the plugin in `homebridge-bticino-hometouch/`. For Home
Assistant see [home-assistant.md](home-assistant.md); both can run at the same
time on the same listener.

Status (2026-10): ring notifications with snapshot, live video with the
entrance panel's sound, answering a ring and talking to the door, and entrance
opening verified on one HOMETOUCH installation (talking from the on-demand live
view is implemented but not yet verified at the door).

## 1. Listener prerequisites

- The listener is installed and registered (see the README).
- IPC is enabled (`BTICINO_IPC_ENABLED=1`) and the Homebridge service user can
  read and write its Unix socket (default `/tmp/bticino-hometouch.sock`).
- For opening entrances, `entrance_open_enabled` and `entrances` are set in the
  listener `config.json` (README → *Opening entrances*).

## 2. Run the plugin in its own Homebridge instance

Start from [`config.child.example.json`](../homebridge-bticino-hometouch/config.child.example.json)
and choose your own bridge `username` and `pin`. The relevant accessory options:

```json
{
  "accessory": "BTicinoHOMETOUCH",
  "name": "Videocitofono",
  "ipcSocket": "/tmp/bticino-hometouch.sock",
  "enableCamera": true,
  "enableHapLive": true,
  "enableTwoWayAudio": true,
  "ffmpegPath": "/opt/homebrew/opt/ffmpeg/bin/ffmpeg",
  "audioFfmpegPath": "<FFmpeg with libspeex and libopus>",
  "standalone": true
}
```

Audio needs, in the listener `config.json`, `"incoming_audio": true` (talking
during a ring) and `"audio_ffmpeg"`: an FFmpeg built with `libspeex`, because
the gateway exchanges audio only in Speex. Homebrew's FFmpeg lacks it; the one
bundled with `ffmpeg-for-homebridge` has it. The plugin option `liveAudio`
(default on) adds the panel's sound to the on-demand live view; `false`
restores the previous video-only behaviour.

**`standalone: true` is required for ring notifications.** It publishes the
doorbell as its own HomeKit accessory with the *Video Doorbell* category.
Bridged, HomePods chime and Home fetches snapshots, but no iPhone or iPad
receives a notification. Snapshots are scaled to the size Home requests and
logged (`Snapshot inviato: richiesta=…`).

## 3. Add it to Apple Home

1. Start the instance. The log shows
   `Please add [Videocitofono XXXX] manually in Home app. Setup Code: …`
   (the bridge's own pairing code).
2. Home → **+** → *Add Accessory* → *More options* → **Videocitofono XXXX** →
   enter the code.
3. In *Streaming & Recording* choose **Stream** for both *at home* and *away*.
4. Enable **Doorbell notifications** in the accessory settings and assign a room.

The bridge itself only needs to be added if you use the experimental in-call
`BTicinoCallLock` ("Apri ingresso").

## 4. Audio and talking

The gateway sends the panel's sound only while it receives audio from the
client, so every call (on-demand live view and answered ring) sends silence to
the door and switches to the iPhone microphone while the talk button in Home is
on. In the live view:

- the panel's sound plays as soon as the video starts (it is often near
  silence: the panel is quiet when nobody is there);
- the talk button sends your voice to the door speaker;
- answering a ring from the notification opens the call's live video; the
  call then lasts until the panel ends it (about a minute), an unanswered ring
  is closed after 35 seconds;
- the panel ends audio calls after about 60 seconds; during an on-demand view
  the plugin opens a new call, with a short gap.

Every 10 seconds the plugin logs `BTicino live audio: panel … B/s, level … dB,
gaps filled …`, which tells whether sound arrives and how loud it is.

## 5. Opening the gates from Apple Home

Run the optional MQTT bridge (`src/bticino_mqtt_bridge.py`, README → *MQTT lock
topics*) and add one Homebridge mqttthing lock per entrance:

```json
{
  "accessory": "mqttthing",
  "type": "lockMechanism",
  "name": "Cancello Scala",
  "url": "mqtt://<broker>:1883",
  "topics": {
    "setLockTargetState": "hometouch/scala/set",
    "getLockTargetState": "hometouch/scala/state",
    "getLockCurrentState": "hometouch/scala/state"
  },
  "lockValues": ["UNLOCK", "LOCK", "JAMMED", "UNKNOWN"]
}
```

Put the locks in the **same room** as the doorbell. Touching and holding the
ring notification then shows live video with the lock controls; Home offers
every lock in the room because it cannot know which entrance rang. The
full-screen camera view in Home on iPhone does not show these controls.

If one device keeps showing stale lock states (for example *Unlocking…*) while
others update, moving the locks to their own child bridge (new `_bridge`
identity) and adding it again fixed it on the test installation.

## 6. Troubleshooting

| Symptom | Check |
| --- | --- |
| HomePod chimes, no notification on any device | `standalone: true`, then re-add the accessory |
| No preview, no live, no snapshot requests in the log | Home stored the camera as off: `"active": false` in `persist/ControllerStorage.*.json`. Choose *Stream* in Home; the hub rewrites the value on every reconnect, so editing the file does not help |
| Accessory "Not responding" on one iPhone only | Rebuild the bridge with a new `username` and re-add it |
| Live view shows no image | Wait about 6 seconds; repeated `source ended: reopening SIP` in the log points to the network path to the HOMETOUCH device |
| Live view or ring call without sound | `audio_ffmpeg` must point to an FFmpeg with `libspeex`; check the `BTicino live audio` log lines |
| After many live views in a row, no video or no audio for minutes | The gateway needs a pause after frequent calls; wait 10–20 minutes. Calls killed without `BYE` are closed at the next call (`STALE_BYE` in `camera-calls.log`) |
| Test without anyone at the door | Send `{"command":"notify_ring"}` to the IPC socket; HomePods will chime |

The HOMETOUCH gateway closes each SIP/TLS connection after about 1024 s; the
listener reconnects in about one second. See also
[plugin architecture](plugin-architecture.md) and
[contextual opening](contextual-unlock.md).
