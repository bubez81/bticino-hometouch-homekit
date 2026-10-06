# Home Assistant integration

`custom_components/bticino_hometouch` connects Home Assistant to the listener's
[network API](../README.md#network-api-for-home-automation-optional). The listener
keeps doing all SIP work; Home Assistant can run on another host.

Status (2026-10): experimental. Tested with Home Assistant 2026.9/2026.10 and the
automated test suite in `tests_ha/`. Verified on one installation: real ring
events, the notification with snapshot and sound, opening from the
notification, live video with the panel's sound.

## 1. Enable the API on the listener host

In the listener `config.json`:

```json
"api": {"enabled": true, "bind": "0.0.0.0", "port": 8790,
        "token_file": "/opt/bticino-sniffer/private/api_token",
        "allowed_clients": ["<Home Assistant IP>"]}
```

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | sudo tee /opt/bticino-sniffer/private/api_token >/dev/null
sudo chmod 600 /opt/bticino-sniffer/private/api_token
```

Restart the listener. Enable `entrance_open_enabled` and `entrances` as well if
Home Assistant should open doors.

## 2. Install the integration

HACS → ⋮ → *Custom repositories* → add
`https://github.com/bubez81/bticino-hometouch-homekit` as an **Integration**,
install **BTicino HOMETOUCH**, restart Home Assistant. Then *Settings → Devices &
services → Add integration → BTicino HOMETOUCH* and enter the listener host,
port (8790) and token. A wrong token or a client outside `allowed_clients` is
reported as an authentication error; a changed token can be updated through
*Reconfigure/Re-authenticate* without removing the entry.

## 3. Entities

| Entity | Purpose |
| --- | --- |
| `event.<device>_campanello` / `doorbell` | `doorbell` event entity; fires `ring` on every call |
| `camera.<device>_telecamera` | snapshots at the size Home Assistant asks for |
| `image.<device>_ultimo_visitatore` | visitor image of the latest ring |
| `button.<device>_apri_<entrance>` | opening pulse per entrance; attribute `entrance` |
| `sensor.<device>_ultima_suonata` | timestamp of the latest ring |
| `sensor.<device>_ingresso_ultima_suonata` | entrance of the latest ring (`not_recognized` until recognised) |
| `binary_sensor.<device>_chiamata_in_corso` | an entrance panel call is active |
| `binary_sensor.<device>_registrazione_sip` | the listener is registered with the gateway (diagnostic) |

Every listener event is also fired on the bus as `bticino_hometouch_event`
(`type`: `ring`, `entrance_detected`, `snapshot_ready`, `call_ended`,
`entrance_open`, `sip_registered`, `sip_disconnected`). For automations prefer the
`event.received` trigger on the doorbell entity.

Entities are unavailable while the event stream is disconnected; the
integration reconnects automatically with backoff.

## 4. Ring notification with the right gate

Import the blueprint
[`ring_notification.yaml`](../blueprints/automation/bticino_hometouch/ring_notification.yaml)
(*Settings → Automations & scenes → Blueprints → Import* with its GitHub URL).
On a ring it waits a few seconds for the entrance to be recognised, then sends a
time-sensitive notification with the snapshot to the selected Companion App
devices and a button that opens **only the entrance that rang**. If the entrance
is not recognised in time, every entrance is offered (up to four). The button in
the notification stays valid for a configurable time (default two minutes).

- **Sound:** the notification plays the iOS sound set in *Notification sound*
  (default `default`). With an Apple Watch on the wrist and the iPhone locked,
  iOS plays it on the watch; with the iPhone in use and the Home Assistant app
  open it may arrive silently.
- **Hold:** holding the notification shows the camera live (without sound).
- **Tap:** *Page opened by tapping the notification* sets the Home Assistant
  path to open. The iOS app opens only paths, not entity dialogs, so point it to
  a dashboard view with the camera, for example a view with a `picture-entity`
  card (`camera_view: live`) whose tap opens the camera dialog with sound.

Re-import the blueprint after updating: earlier versions stopped at every real
ring with `'context' is undefined` and sent no notification.

## 5. Live video (go2rtc)

The camera streams live when the listener advertises an RTSP source:

1. On the listener host install [go2rtc](https://github.com/AlexxIT/go2rtc)
   (official release binary; verify its SHA-256) and run it as an unprivileged
   service with [`packaging/go2rtc.example.yaml`](../packaging/go2rtc.example.yaml):
   API on localhost only, RTSP on the LAN with a password, one `videocitofono`
   stream that runs `src/bticino_live_source.py`.
2. Add to the listener `api` section
   `"live_rtsp_url": "rtsp://bticino:<password>@<listener IP>:8554/videocitofono"`
   and restart the listener. Only authenticated API clients receive this URL.
3. Reload the integration: the camera gains streaming, and Home Assistant's
   built-in go2rtc plays it with WebRTC.

The source starts only while someone watches. During a ring it relays the
call's video; otherwise it places an on-demand camera call and closes it when
the last viewer leaves. It never retries a camera call, because frequent
back-to-back calls were seen to stop delivering video for minutes: when the
camera is busy (for example Apple Home is viewing), when the previous call ended
less than 20 seconds ago, or when no video arrives within 12 seconds, it shows
the latest snapshot instead. Expect the first image after about 5 seconds. Add
`--log <file>` to the stream command to see what the source did.

**Audio.** The on-demand call also carries the entrance panel's audio, as in the
official app. The gateway accepts audio only as Speex (8 kHz) in both directions
and sends the panel's sound only while it receives audio from the client, so
the camera call transmits silence and transcodes what it receives to Opus for
WebRTC. Sending Speex needs an FFmpeg built with `libspeex`; Homebrew's FFmpeg
lacks it, while the one bundled with `ffmpeg-for-homebridge` has it. Set its
path as `audio_ffmpeg` in the listener `config.json` (or `BTICINO_AUDIO_FFMPEG`);
the listener does not need a restart. Without such an FFmpeg the stream keeps
working with video only. Add `--no-audio` to the stream command to never ask for
audio. During a ring the stream remains video only. Home Assistant's camera
players start muted: unmute them in the camera dialog.

**Talking (experimental).** go2rtc can pass a browser microphone to the camera
call through a backchannel source, `src/bticino_talk_relay.py`, which forwards
the viewer's 8-kHz A-law audio to the call's talk port; the call sends it to
the door instead of silence. go2rtc's RTSP server does not relay the
backchannel, so Home Assistant's built-in go2rtc cannot be used for it: the
browser must reach the listener host's go2rtc directly, with the *WebRTC
Camera* card (`media: video,audio,microphone`), its integration pointed at that
go2rtc (API with username and password, WebRTC port 8555) and Home Assistant
opened over HTTPS (browsers grant the microphone only to secure pages). On the
test installation the browser microphone reached the call; playback at the door
and answering a ring from Home Assistant are not implemented or verified yet.

## 6. MQTT locks and HomeKit

Existing MQTT locks fed by `bticino_mqtt_bridge.py` keep working; the buttons
above are an alternative that does not need MQTT.

If you expose this doorbell to Apple Home through Home Assistant's HomeKit
Bridge, publish it as its own accessory: Apple Home sends ring notifications only
for accessories advertised with the *Video Doorbell* category, which bridged
accessories do not carry. The same limitation led the Homebridge plugin to its
`standalone` option.

## 7. Tests

```sh
pip install pytest-homeassistant-custom-component PyTurboJPEG
python -m pytest tests_ha -q
```

The tests start the real listener API module on loopback and drive the
integration in a test Home Assistant instance: config flow, entities, ring and
entrance events, opening and camera images. The blueprint is validated with Home
Assistant's own blueprint and automation schemas and run end to end: ring,
notification with the entrance that rang, opening from the notification
button.
