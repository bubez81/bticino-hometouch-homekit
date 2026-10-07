# Home Assistant

The `bticino_hometouch` integration connects Home Assistant directly to a
BTicino **HOMETOUCH** video door entry system. Nothing else to install: no
Homebridge, no add-on, no separate service. It registers as one more phone of
the system and gives you:

- a **doorbell** event, for automations and the ready-made notification
  blueprint;
- the **camera**: the latest picture and the **live view with sound**;
- one **opening button per gate**;
- sensors: last ring, gate of the last ring, call in progress, SIP
  registration;
- a **dashboard card** to watch, **talk**, **answer a ring** and open the
  gates.

> Experimental, verified on one HOMETOUCH installation. Not affiliated with
> BTicino or Legrand. Use it only with your own system.

## You need

- **Home Assistant 2026.7 or newer** (Home Assistant OS, Container or Core;
  64-bit ARM such as a Raspberry Pi 4/5, or x86-64) with
  [HACS](https://hacs.xyz).
- A **dedicated Door Entry account**: create a new account with its own email
  address, have the system's owner invite it from the Door Entry app, and
  accept the invitation once. Do not use your personal account: Home Assistant
  adds a phone to whatever account you give it.
- A **free phone slot** on the system (HOMETOUCH accepts about 20). If you
  also use the Homebridge plugin, Home Assistant takes a second slot: both
  ring, either can open.
- Internet the first time: the integration downloads a static FFmpeg with the
  Speex codec the gateway needs, from
  [ffmpeg-for-homebridge](https://github.com/homebridge/ffmpeg-for-homebridge)
  (about 30 MB, checked against its published SHA-256). Home Assistant's own
  FFmpeg lacks Speex.

## Install

1. **HACS** → ⋮ → *Custom repositories* → repository
   `https://github.com/bubez81/bticino-hometouch-homekit`, type
   **Integration** → *Add*. Or open it directly:

   [![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bubez81&repository=bticino-hometouch-homekit&category=integration)

   Then *Download* **BTicino HOMETOUCH** and **restart Home Assistant**.
2. *Settings → Devices & services → Add integration →* **BTicino HOMETOUCH**.
   Sign in with the dedicated account and choose the system if you have more
   than one. In about a minute the phone is created (it appears in the Door
   Entry app as *Home Assistant BTicino*) and FFmpeg is downloaded. The
   password is used only for this step and never stored.
3. On the integration, **Configure** → **Entrances**: a name and the lock
   address of each gate, separated by commas, for example
   `Scala=20, Esterno=21`. The entrance panel's own lock is usually `20`, a
   second lock `21`. Each gate gets an *Open …* button.
4. **Ring the doorbell once.** The live view needs the gateway at home: the
   cloud server refuses camera calls. At the first ring the integration finds
   the gateway's local address by itself and checks it by its certificate (log:
   `Gateway di casa trovato`). If you know the address, you can also enter it
   under **Configure** → **Gateway address (local network)**.

Private files (certificate, key, logs, pictures) are kept in
`/config/bticino_hometouch` with owner-only permissions and are included in
Home Assistant backups.

## The video door entry card

The integration brings its own dashboard card, nothing else to install: edit
a dashboard → *Add card* → **BTicino HOMETOUCH** (or YAML
`type: custom:bticino-hometouch-card` with `entity:` the camera). It shows the
live view and:

- **Talk**: turns the microphone on and off during the live view; the panel
  plays your voice;
- **Answer** while someone is ringing: answers the call, turns the microphone
  on and plays the panel's sound through the card; **Hang up** ends it (the
  panel ends answered calls after about a minute);
- one button per gate, with a confirmation.

The browser gives the card the microphone only when Home Assistant is opened
over **HTTPS** (for example through Home Assistant Cloud or your own
certificate) or on the same device. The Companion app works when its server
address is HTTPS.

Answering from Home Assistant takes the call: other phones of the system, for
example Apple Home through the plugin, stop ringing.

## Live view

Add the camera to a dashboard, for example a *Picture entity* card with
*Camera view: live*, or open the camera entity. The live view opens a call to
the entrance panel's camera through Home Assistant's built-in go2rtc and
closes it when nobody is watching; during a ring it shows the call's video.
Players start muted: unmute them for the panel's sound.

After many live views in a row the gateway may stop sending video for a few
minutes; the integration then shows the latest picture instead of retrying.

## Ring notification with the right gate

Import the blueprint
[`ring_notification.yaml`](../blueprints/automation/bticino_hometouch/ring_notification.yaml):
*Settings → Automations & scenes → Blueprints → Import blueprint*, with the
address

```
https://github.com/bubez81/bticino-hometouch-homekit/blob/main/blueprints/automation/bticino_hometouch/ring_notification.yaml
```

For **Page opened by tapping the notification** choose a dashboard view with
the card: tapping the notification, or its **Rispondi** button, opens the card,
where you answer.

Create an automation from it and choose the doorbell event, the last ring
entrance sensor, the camera, the opening buttons and the phones to notify
(Home Assistant Companion app). On a ring it sends a time-sensitive
notification with the picture and a button that opens the gate that rang (or
one button per gate if it is not recognised in time). Holding the
notification shows the live camera; tapping it opens the page set in *Page
opened by tapping the notification*, for example a dashboard view with the
camera. Title, message and sound can be changed.

Every listener event is also fired on the bus as `bticino_hometouch_event`
(`type`: `ring`, `entrance_detected`, `snapshot_ready`, `call_ended`,
`entrance_open`, `sip_registered`, `sip_disconnected`).

## Updating and removing

- **Update**: HACS shows the update; install it and restart Home Assistant.
- **Remove**: delete the integration in *Devices & services*, then remove the
  phone *Home Assistant BTicino* from the Door Entry app to free its slot.
  Deleting `/config/bticino_hometouch` removes its private files.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Setup stops with an error from the cloud | Check the dedicated account in the Door Entry app (invitation accepted), then repeat the setup: a phone left by an interrupted setup is reused, not duplicated |
| The live view does not start | Ring once so the gateway at home is found, or enter its address under **Configure**; the log line `Live: no video from the camera` includes the camera call's details |
| Camera calls fail with `407` in the log | The phone is connected to the cloud server: ring once or set the gateway's address under **Configure** |
| Authentication errors after changes in the Door Entry app | ⋮ on the integration → **Reconfigure**: sign in again and the phone's credentials are re-read from the cloud (nothing new is created) |
| A gate does not open | Check its address under **Configure** → **Entrances** |
| No notification | Check the automation made from the blueprint and the Companion app's notification permissions |
| The card says the microphone works only over HTTPS | Open Home Assistant through its HTTPS address |
| The card is missing from *Add card* | Reload the page (the card is added by the integration at start-up) |

The integration logs under `custom_components.bticino_hometouch`; the
listener's lines carry the `[listener]` prefix, and calls to the camera are
logged in `/config/bticino_hometouch/camera-calls.log`. *Download diagnostics*
on the integration describes the phone's files without their contents. When
reporting a problem,
[open an issue](https://github.com/bubez81/bticino-hometouch-homekit/issues/new/choose)
with the time it happened and the relevant log lines; remove addresses and
account names first.

## Tests

```sh
pip install pytest-homeassistant-custom-component PyTurboJPEG
python -m pytest tests_ha -q
```
