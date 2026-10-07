# Home Assistant

The `bticino_hometouch` integration connects Home Assistant directly to a
BTicino **HOMETOUCH** system: no Homebridge, no separate service. It registers
as one more phone of the system and gives you:

- a **doorbell** event (for automations and the notification blueprint);
- the **camera**, with the latest picture and **live video with sound**;
- one **opening button per entrance**;
- sensors: last ring, entrance of the last ring, call in progress, SIP
  registration.

> Experimental, verified on one HOMETOUCH installation. Not affiliated with
> BTicino or Legrand.

## You need

- Home Assistant 2026.7 or newer (Home Assistant OS, Container or Core) and
  HACS.
- A **dedicated Door Entry account**: a new account with its own email
  address, invited to the system by its owner in the Door Entry app (accept the
  invitation once). Do not use your personal account.
- A free phone slot on the system (HOMETOUCH accepts about 20). If you also use
  the Homebridge plugin, Home Assistant uses a second slot: both ring, either
  can answer or open.
- Internet access the first time: the integration downloads a static FFmpeg
  with the Speex codec the gateway needs (from
  [ffmpeg-for-homebridge](https://github.com/homebridge/ffmpeg-for-homebridge),
  about 30 MB, verified by its SHA-256), because Home Assistant's own FFmpeg
  lacks it.

## Install

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/bubez81/bticino-hometouch-homekit` as an
   **Integration** → install **BTicino HOMETOUCH** → restart Home Assistant.
2. *Settings → Devices & services → Add integration → BTicino HOMETOUCH*. Sign
   in with the dedicated account and choose the system. The phone shows up in
   the Door Entry app as *Home Assistant BTicino*.
3. *Configure* (on the integration) → **Entrances**: name and lock address of
   each gate, for example `Scala=20, Esterno=21`. The entrance panel's own lock
   is usually `20`, a second one `21`. Each entrance gets an *Open …* button.

Private files (certificate, key, logs, pictures) are kept in
`/config/bticino_hometouch` and are part of Home Assistant backups.

## Live video

The camera streams through Home Assistant's built-in go2rtc. A live view opens
a call to the entrance panel's camera and closes it when nobody watches;
during a ring it shows the call's video. The camera's players start muted:
unmute them for the panel's sound. After many live views in a row the gateway
may need a few minutes of rest; in the meantime the latest picture is shown.
Talking from Home Assistant is not available yet.

## Ring notification with the right gate

Import the blueprint
[`ring_notification.yaml`](../blueprints/automation/bticino_hometouch/ring_notification.yaml)
(*Settings → Automations & scenes → Blueprints → Import*). On a ring it sends a
time-sensitive notification with the picture and a button that opens the
entrance that rang (or every entrance if it is not recognised in time).
Holding the notification shows the live camera; tapping it opens the page set
in *Page opened by tapping the notification*, for example a dashboard view with
the camera.

Every listener event is also fired on the bus as `bticino_hometouch_event`
(`type`: `ring`, `entrance_detected`, `snapshot_ready`, `call_ended`,
`entrance_open`, `sip_registered`, `sip_disconnected`).

## Troubleshooting

The integration logs under `custom_components.bticino_hometouch`; the
listener's lines carry the `[listener]` prefix. Calls to the camera are logged
in `/config/bticino_hometouch/camera-calls.log`. When reporting a problem,
[open an issue](https://github.com/bubez81/bticino-hometouch-homekit/issues/new/choose)
with the time it happened and the relevant log lines; remove addresses and
account names first.

## Tests

```sh
pip install pytest-homeassistant-custom-component PyTurboJPEG
python -m pytest tests_ha -q
```
