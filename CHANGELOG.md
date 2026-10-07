# Changelog

Changes are dated by publication. This project remains experimental; entries
describe implemented behavior separately from physical validation. The
Homebridge plugin is published on npm (`homebridge-bticino-hometouch`); the
Home Assistant integration is installed from this repository through HACS.

## 2026-10-07

### Added

- All-in-one Homebridge plugin (platform `BTicinoHometouch`): the plugin starts
  and supervises the bundled Python listener (restart with back-off, clean stop
  with BYE), keeps its private files in Homebridge's storage
  (`bticino-hometouch/`, owner-only), and uses `ffmpeg-for-homebridge`, which
  includes Speex and Opus. One Video Doorbell accessory carries the camera,
  two-way audio and **one lock per entrance** (name and address configurable;
  opening is a pulse). An option enables the network API for the Home Assistant
  integration and creates its token.
- Settings page in the Homebridge UI: sign in with the dedicated Door Entry
  account, choose the system, *Configura* creates the bridge's SIP endpoint and
  certificate, writes the private files and adds the entrance panel's gate
  (address `dev + where`, as the official app builds it); *Prova apertura*
  checks each saved gate. The password is never stored. A private Python
  environment with `pyzipper` is created on first use.
- `bticino-onboard --list-devices`: read-only list of device types and
  addresses of the system, without names.

### Changed

- The onboarding's provisioning step is a reusable function
  (`provision`), used by the command line and by the plugin's settings page
  (`src/bticino_plugin_setup.py`). The command-line behaviour is unchanged.
- `probe-camera.py` finds the listener through `BTICINO_LISTENER`.
- The plugin keeps retrying the listener connection instead of giving up when
  the listener starts after Homebridge.
- Documentation reduced to one guide: the plugin README (also the npm page)
  explains requirements, installation in three steps, use, updates and
  troubleshooting; the repository README is a short overview. Research notes,
  the old architecture, onboarding and validation pages were removed (they
  remain in the Git history). The Home Assistant guide stays until the
  integration is reworked.
- Plugin package 0.9.0: display name, repository links, Homebridge 1.6–2.x.

### Fixed (plugin 0.9.5, Apple Home live view)

- The Apple Home live view stopped showing video after the plugin moved to
  the FFmpeg of ffmpeg-for-homebridge (8.0): packets reached the iPhone and
  iPad, which kept asking for a keyframe and closed the view after about 30 s.
  The encoder now follows the recipe of homebridge-plugin-utils (used by
  homebridge-unifi-protect with the same FFmpeg build): no upscaling (camera
  aspect ratio, at most the requested height), no B-frames, `veryfast`, rate
  control with `maxrate`/`bufsize`, a keyframe every 5 s, corrupt input packets
  dropped, RTP packets of at most 1200 bytes flushed one by one. Verified on
  the test installation: the live view shows again, first video about 5 s
  after the request (5.8–7.4 s before).

### Release

- Plugin 0.9.5 and Home Assistant integration 0.2.1 (keyframe request on
  camera calls, latest-call diagnostics).

### Changed (camera calls)

- On-demand camera calls ask the camera for a keyframe (SRTCP PLI) as soon as
  video arrives, three times one second apart, as incoming calls already do.
  Observed before: an Apple Home live view sent its first video 5.8 s after
  the request (camera answered after about 2.5 s), while during a ring, with
  the keyframe request, it took 1.4–2 s. `--no-keyframe-request` turns it off.

### Added (Home Assistant diagnostics)

- *Download diagnostics* includes the latest camera call's summary: SIP
  status codes, packet counts and the bytes of speech sent to the panel
  (`talk_bytes`), without keys or file paths.

### Added (ring notification blueprint)

- An **Answer** button (label configurable, default *Rispondi*, empty to
  omit) opens the page of *Page opened by tapping the notification*, where
  the dashboard card answers the call.

### Added (Home Assistant integration 0.2.0, talk)

- Dashboard card `custom:bticino-hometouch-card`, served and registered by
  the integration: live view, **Talk** (microphone during the live view),
  **Answer** / **Hang up** for a ring with the panel's sound played by the
  card, and the gates' buttons with confirmation.
- Talk channel: a WebSocket on a signed path (Home Assistant login). The
  microphone arrives as 16-bit PCM at 8 kHz and goes to the listener's talk
  port as A-law, the input the Apple Home plugin uses; answering a ring uses
  the listener's `attach_incoming` / `answer_incoming` / `release_incoming`
  and streams the panel's sound back as 16-bit PCM at 16 kHz.

### Changed (documentation)

- The repository README presents both ways to use the system, Apple Home
  (Homebridge plugin) and Home Assistant (HACS integration), with a comparison
  and the Home Assistant steps in short; the Home Assistant guide covers
  requirements, installation from HACS, the live view, the notification
  blueprint, updating, removing and troubleshooting.

### Added (listener, plugin 0.9.4)

- When only the cloud SIP server is configured, the listener learns the
  gateway's local address from the first ring (media and Contact addresses of
  the INVITE, home-network ranges only), checks it with a TLS connection whose
  certificate must be signed by the BTicino CA and name the plant's SIP
  domain, saves it in `runtime/gateway-address.json` and connects to it from
  then on; the camera probe uses it too. Verified against the real gateway
  (accepted) and another address (refused); learning from a real ring through
  the cloud is not yet observed.
- Plugin 0.9.4 bundles the setup and SIP fixes of this day.

### Changed (Home Assistant integration 0.2.0)

- Options reload the integration through `OptionsFlowWithReload` (the update
  listener is deprecated for Home Assistant 2026.12).

### Added (Home Assistant integration 0.2.0, local gateway)

- Option *Gateway address (local network)*: the listener connects to the
  HOMETOUCH gateway at home instead of the cloud SIP server. Camera calls (the
  live video) are answered by the gateway; the cloud server challenges them
  for a password the endpoint cannot provide (407 twice, observed).

### Fixed (SIP authentication)

- Camera calls from a newly created phone were refused with 407 twice: when
  the SIP server asks for a password, the user name must be the endpoint's
  `Username` from the cloud, not the account's user part. The onboarding now
  saves it with the credentials and the listener and the camera probe use it
  (older files fall back to the previous behaviour).
- `bticino_plugin_setup.py refresh` re-reads the bridge's endpoint from the
  cloud and updates the saved credentials without creating anything; in Home
  Assistant it is the integration's *Reconfigure* step.

### Added (Home Assistant integration 0.2.0, diagnostics)

- *Download diagnostics* describes the phone's private files without their
  values: which files exist, the SIP password's length and character class,
  whether the certificate's name matches the account and the key matches the
  certificate, and its validity.

### Added (camera probe)

- When a camera call is challenged for authentication (401/407), the call log
  says who challenged it (the plant's SIP domain or another realm, the Server
  header and the reason), without nonces or account identifiers.

### Fixed (Home Assistant integration 0.2.0, live)

- Two requests opened at the same time (go2rtc and Home Assistant) share one
  camera call; before, the second one was refused and stayed empty.
- With no saved picture to fall back on, the live ends instead of loading
  forever.
- When a camera call brings no video, the latest call's lines from
  `camera-calls.log` are written to the Home Assistant log.

### Fixed (Home Assistant integration 0.2.0, reload)

- Changing the entrances failed to reload the integration ("failed unload"):
  the live view's cleanup returned a value Home Assistant took for a task.
- Opening buttons of entrances removed or renamed in the options are removed
  instead of staying unavailable.

### Fixed (Home Assistant integration 0.2.0, plugin setup)

- Phone creation stopped with "Risposta cloud non valida (HTTP 201 … byte=0)"
  when the cloud confirmed the new SIP endpoint without a body: an empty 2xx
  answer is accepted and the account is read from the endpoint list. A phone
  with the same name left by an interrupted setup is reused instead of creating
  a second one.

### Added (Home Assistant integration 0.2.0)

- The integration works on its own, without Homebridge or a separate listener:
  the config flow signs in with the dedicated Door Entry account, lets you
  choose the system and creates the integration's own phone (*Home Assistant
  BTicino*); the bundled listener (`custom_components/bticino_hometouch/listener`,
  kept in sync with `src/` by `tools/sync_listener.py`) runs as a supervised
  child process with restart back-off. Private files live in
  `/config/bticino_hometouch`. A static FFmpeg with Speex
  (ffmpeg-for-homebridge v2.2.2) is downloaded once and checked against its
  SHA-256, because Home Assistant's FFmpeg lacks Speex.
- Live video with sound through Home Assistant's built-in go2rtc: a local
  MPEG-TS view (H.264 + AAC) opens an on-demand camera call or relays the ring
  call's video, and falls back to the latest picture.
- Options: entrances as `Name=address` pairs, one opening button each.
- Without the `openssl` command (Home Assistant OS), the onboarding creates the
  key and certificate request with the `cryptography` library.
- `BTICINO_LIVE_AUDIO_CODEC=aac` makes the camera probe send AAC instead of
  Opus.

### Changed (Home Assistant integration 0.2.0)

- Entries created by earlier versions, connected to an external listener's
  API, keep working; the Home Assistant guide describes the standalone setup.

### Added (plugin 0.9.3)

- The plugin keeps its own log, `bticino-hometouch/plugin.log` (rotated at
  1 MB, three files), with the same lines it writes to the Homebridge log.
- **Scarica diagnostica** in the plugin settings: one anonymised text file with
  versions, FFmpeg codecs, setup and configuration summary, checks (gateway
  reachable, listener, current call, entrances) and the recent plugin and
  camera-call logs. Addresses, SIP accounts and domain, e-mail addresses, MAC
  addresses, setup codes, keys, tokens and home-folder user names are replaced
  by placeholders. A GitHub issue form asks for this file.

### Fixed (plugin 0.9.3)

- *Prova apertura* used the default listener socket even when `ipcSocket` was
  configured.

### Changed (plugin 0.9.2)

- The project banner, badges and the support section are back in the
  repository README and also shown on the plugin's npm page; the package lists
  its funding links. The empty `bin` field was removed from `package.json`.

### Fixed (plugin 0.9.1)

- A listener that stopped with an error left its IPC socket behind, and every
  following start failed with "Address already in use" until the file was
  removed by hand. The listener now removes a dead socket at start (and refuses
  to start if another listener still answers) and deletes its own at exit.
- The private files of the setup are stored relative to the data folder, and
  the listener's configuration is written again at every restart. Moving the
  data folder (for example from a separate Homebridge instance into the main
  one) no longer leaves paths pointing to the old location.

### Validation

- Python 151 tests (1 skipped) and the plugin's tests, including a platform
  test with a fake listener and the restart policy.
- The test installation was migrated to the plugin: same Apple Home pairing
  (no re-adding), listener registered, live view with sound, both gates opened
  from the locks inside the doorbell and returned to locked, Home Assistant
  reconnected with its existing token. The separate listener service, the
  dedicated MQTT bridge and the mqttthing locks were retired.
- Python 164 tests (1 skipped), Home Assistant 20 tests, plugin tests.
- Home Assistant (OS 18.3, Raspberry Pi 5) migrated to the standalone
  integration: phone created and registered, entrances and the ring
  notification automation kept their entity IDs, the live view started
  through the built-in go2rtc **with the panel's sound** once the gateway at
  home was used. The cloud server's 407 on camera calls was observed before.
- Plugin 0.9.4 installed from npm on the test installation.
- Not yet verified on the real system: talking and answering from the
  dashboard card, the notification's *Rispondi* button, and learning the
  gateway's address from a ring through the cloud.

## 2026-10-06

### Added

- Two-way audio during a ring (Apple Home). The listener now answers the
  panel's call with Speex when offered and, as on camera calls, sends a
  continuous encrypted stream from the start of the call: silence, or the
  iPhone microphone while Home's talk button is on
  (`src/bticino_incoming_audio.py`). The panel's sound is decoded to PCM for
  the plugin, which reuses the live-view audio path (jitter buffer, HomeKit
  Opus, microphone to the talk port). Before, the call carried no client
  audio, so the panel sent none and talking never reached the door. Verified
  with a real ring: notification, live video, panel sound and speech at the
  door. An answered call is no longer cut 35 seconds after the ring (a limit
  meant for unanswered rings); it lasts until the panel ends it, at most
  three minutes.
- While a ring's snapshot is being taken, the snapshot endpoint serves the
  latest real image instead of a flat placeholder. Test rings with a real image
  produced an Apple Home notification while real rings, served the
  placeholder, did not; whether this was the cause is not yet confirmed.
- Real audio in the Apple Home live view (Homebridge plugin, `liveAudio`,
  default on with `enableTwoWayAudio`). Until now the on-demand live view sent
  HomeKit synthetic silence (logged as `HomeKit diagnostic audio: silence
  only`), because the camera call had audio disabled. The plugin now asks for
  an audio call (`start_call` with `audio` and `audio_port`), re-encodes the
  entrance panel's sound for HomeKit, and, while Home unmutes the microphone,
  sends the iPhone's voice to the call's talk port. `test-live-audio.js`
  checks both directions with real encoders. A 200-ms jitter buffer feeds the
  HomeKit encoder at a steady rate: on the test installation the panel's audio
  arrived in bursts (gaps of 80–180 ms, once 742 ms, then catch-up), which made
  it come and go in Apple Home. Panel sound was heard in Apple Home; speaking
  from Home to the door is not yet verified. The entrance panel ends a call with
  audio after about 60 seconds; the plugin then opens a new one.
- Ring blueprint: the notification plays a sound (`sound`, default iOS
  `default`) and tapping it opens a configurable Home Assistant page
  (`tap_url`; the iOS app opens paths, not entity dialogs). Holding it shows
  the camera live.
- Speaking from Home Assistant: a go2rtc backchannel source
  (`src/bticino_talk_relay.py`) forwards the viewer's microphone to the camera
  call, which sends it to the door instead of silence. On the test
  installation the browser microphone reached the call; playback at the door
  is not yet verified. go2rtc's RTSP server does not relay the backchannel, so
  the browser must use the listener host's go2rtc (WebRTC Camera card).
- Entrance-panel audio in the Home Assistant live stream. With the new
  `--audio` option the camera probe offers Speex 8 kHz send/receive, as the
  official app does, sends encrypted Speex silence (the gateway transmits the
  panel's sound only while it receives client audio), and adds the received
  audio to the MPEG-TS stream as Opus. IPC `start_call` accepts `"audio": true`;
  the go2rtc source requests it by default (`--no-audio` turns it off) and
  forwards any audio track. Sending needs an FFmpeg with `libspeex`, configured
  as `audio_ffmpeg` in the listener config or `BTICINO_AUDIO_FFMPEG`. Without
  `--audio` the offer is unchanged, so Apple Home live view is not affected.
- Camera calls started through IPC are logged to `camera-calls.log` next to the
  probe (`BTICINO_CAMERA_LOG`, mode 600, rotated at 1 MB): start time, progress
  every 10 s with timestamps, remote hang-up, end of call and FFmpeg errors.
  The probe prints no keys, addresses or credentials. Previously this output was
  discarded, so a live stream that stopped could not be explained.
- Validation: on the test installation the gateway accepted the Speex offer and
  sent about 47 audio packets per second; a 20-second call produced H.264
  400×288 with Opus 48 kHz. Audio with a G.711 offer, or without client audio,
  was never received. Panel sound was heard in the Home Assistant live view.
- Live video for Home Assistant through go2rtc. `src/bticino_live_source.py` is
  a go2rtc `exec:` source: during a ring it relays the call's video, otherwise
  it places an on-demand camera call (`start_call`) and closes it when the last
  viewer leaves. It never retries a camera call: when the camera is busy, when
  the previous call ended less than 20 seconds ago, or when no video arrives
  within 12 seconds, it keeps the stream alive with the latest snapshot. It
  relays through FFmpeg's stdin so no FFmpeg process outlives it, starts a
  watchdog that closes the camera call if the source is killed, and can log to
  a file (`--log`), since go2rtc hides an exec source's output.
- The API advertises an optional `live_rtsp_url` (listener `api.live_rtsp_url`,
  validated by `validate_config.py`) to authenticated clients only. The Home
  Assistant camera then supports streaming and plays it with WebRTC through
  Home Assistant's built-in go2rtc.
- `packaging/go2rtc.example.yaml` and a *Live video* section in
  `docs/home-assistant.md`.

### Validation

- On the test installation the go2rtc RTSP stream delivered H.264 400×288 from
  an on-demand camera call, and no process or call remained after the viewer
  left. Three manual camera calls each delivered about 575 RTP packets in 25 s.
  With Home Assistant reopening a failing stream and the source retrying, dozens
  of back-to-back camera calls stopped delivering video for several minutes,
  while spaced calls always worked. Removing the retry and adding the cooldown
  fixed it: Home Assistant played the live stream from a single on-demand call.

### Fixed

- Ring blueprint: every real ring stopped with `UndefinedError: 'context' is
  undefined`, so Home Assistant sent no notification. The notification tag now
  uses the run time. Re-import the blueprint. `tests_ha` now runs the
  automation end to end (ring, notification with the entrance that rang,
  opening from the notification button) instead of validating the schema only.
- Homebridge plugin: random RTP SSRCs above 2^31-1 made FFmpeg refuse the
  stream ("Error setting option ssrc … Result too large"), so about half of
  the Apple Home live views lost their audio after a few seconds, and the same
  could stop the video encoder. SSRCs are now chosen in FFmpeg's range. The
  plugin also no longer logs `Error: Not running` when the encoder flushes
  packets while a session closes.
- Closing a camera call (`stop_call`, end of a live view) did not work: the
  probe imported the listener module, whose import-time SIGTERM handler replaced
  the probe's own. The probe ignored the stop request, was killed after 8 s
  without sending BYE to the gateway, and left its FFmpeg processes running.
  It now installs its handlers after loading the listener and ends the call
  with BYE within a few seconds; the Speex sender also has a time limit as a
  safety net. Calls left open by the gateway probably explain why frequent
  on-demand calls stopped delivering video for minutes.
- After a listener restart the API reported no last ring until the next call,
  so Home Assistant showed *unknown* for the last ring and visitor. The
  listener now restores the last ring time from the newest snapshot file name
  (the entrance of that ring is not stored and stays unknown). The Home
  Assistant image entity also picks up that time when the listener restarts
  while Home Assistant is running.
- The last-entrance sensor used `unknown` for an unrecognised entrance, which
  Home Assistant treats as its reserved *unknown* state; the option is now
  `not_recognized` (shown as *Non riconosciuto*). Automations comparing the
  sensor with `unknown` must use `not_recognized`.
- Ring blueprint: the wait for the entrance no longer ends immediately when
  the sensor resets at the start of a ring; it waits for a recognised entrance.
  Re-import the blueprint to update it.

## 2026-10-05

### Added

- `standalone` option for the `BTicinoHOMETOUCH` accessory. The doorbell is
  published as its own HomeKit accessory with the **Video Doorbell** category
  (18), with the Doorbell service primary and the camera, two-way audio and
  snapshots attached, instead of being bridged. On the test installation,
  doorbell notifications reached iPhone/iPad only in this mode: while bridged,
  HomePods chimed and the hub fetched snapshots, but no device was notified.
- Snapshots are scaled and letterboxed to the resolution HomeKit requests
  (for example 1280×720 or 640×360) with FFmpeg, as Camera-ffmpeg and
  UniFi Protect do. If scaling fails, the original image is sent unchanged.
- Snapshot diagnostics in the Homebridge log: requested size, size sent,
  duration, and errors, which were previously silent.
- `test-standalone.js` (mock HAP) included in `npm test`.
- Listener entrance opening (opt-in, `entrance_open_enabled`): named entrances
  with lock addresses, a configurable press/release pulse sent as SIP MESSAGE on
  the registered connection, IPC commands `open_entrance` and `entrance_status`,
  logged SIP responses, and release retry across reconnects. Configuration is
  checked by `validate_config.py`. This is the first step toward replacing
  installation-specific gate scripts and toward a Home Assistant integration.
- Optional MQTT bridge (`src/bticino_mqtt_bridge.py`, launchd plist): entrance
  locks on `<prefix>/<entrance>/set|state` and `<prefix>/status`, backed by the
  listener's `open_entrance`. Retained commands are ignored and repeated commands
  during a pulse are dropped. It is a drop-in replacement for custom gate
  gateways driving mqttthing or Home Assistant MQTT locks.
- Optional network API (`src/bticino_api.py`, `api` in config): bearer-token
  REST endpoints for info, state, scaled snapshots and entrance opening, plus a
  Server-Sent Events stream (ring, entrance detected, snapshot ready, call ended,
  entrance opening, SIP registration). Standard library only; allowed clients
  configurable; validated by `validate_config.py`. It is the foundation for the
  Home Assistant integration.
- Home Assistant integration `bticino_hometouch` (HACS, `custom_components/`):
  config flow with re-authentication, `doorbell` event entity, camera with
  scaled snapshots, last-visitor image, one opening button per entrance (with an
  `entrance` attribute), last-ring and last-entrance sensors, call and SIP status
  binary sensors, and `bticino_hometouch_event` bus events. It follows the API's
  event stream with automatic reconnection. See `docs/home-assistant.md`.
- Blueprint `blueprints/automation/bticino_hometouch/ring_notification.yaml`:
  time-sensitive mobile notification with snapshot and a button for the entrance
  that rang, or for every entrance when it is not recognised in time.
- `tests_ha/` (pytest-homeassistant-custom-component) and a CI job: the real API
  server drives the integration in a test Home Assistant, and the blueprint is
  validated with Home Assistant's own schemas.
- The API closes open event streams on shutdown instead of leaving them waiting.

### Changed

- `config.schema.json` accepts `standalone`, `serialNumber`, `ffmpegPath` and
  `audioFfmpegPath`; the last two were already used by the plugin.
- `config.child.example.json` enables `standalone`.

### Fixed

- CI: the Homebridge job installs FFmpeg, and `test-incoming-call.js` honors
  `BTICINO_TEST_FFMPEG` like the other media tests. Previously the job failed
  on Linux because the test looked for the macOS Homebrew path.

### How to use the doorbell now

1. Set `"standalone": true` on the `BTicinoHOMETOUCH` accessory and restart the
   dedicated Homebridge instance. The log shows
   `Please add [<name> XXXX] manually in Home app. Setup Code: …`, which is
   the bridge's own pairing code.
2. In Apple Home, remove the previously bridged doorbell if it remains, then
   add the new accessory via **Add Accessory → More options**. Choose
   **Stream** for streaming, enable **Doorbell notifications** and assign a room.
3. Put the lock accessories in the doorbell's room: touching and holding the
   ring notification then shows live video with their controls. Home shows
   every lock in the room; it cannot know which entrance rang. The full-screen
   camera view in Home on iPhone does not show these controls.
4. A `BTicinoCallLock` stays on the bridge. If the bridge is not paired in Home,
   the lock is not available.

### Documentation

- README section *Choose your home platform* comparing Homebridge and Home
  Assistant, with short setup steps for each.
- New [Homebridge guide](docs/homebridge.md): plugin configuration with
  `standalone`, pairing and Home settings, gates through the MQTT bridge and
  mqttthing, troubleshooting. The [Home Assistant guide](docs/home-assistant.md)
  covers the API, HACS installation, entities and the blueprint.

### Troubleshooting notes from the test installation

- Live view and snapshots stopped completely after re-adding the accessory:
  Home had stored the camera as off, persisted as `"active": false` in
  `persist/ControllerStorage.*.json`. Home rewrites this value whenever its hub
  connects, so it must be changed in Home, not on disk.
- A ring can be simulated without the outdoor panel by sending
  `{"command":"notify_ring"}` to the listener IPC socket. HomePods will chime.
- The HOMETOUCH gateway closes each SIP/TLS connection after about 1024 s. The
  listener reconnects and re-registers in about one second.

### Validation and remaining limits

- Simulated rings delivered notifications with a snapshot on iPhone/iPad.
  Notification controls for locks in the same room were verified. A real
  outdoor ring in standalone mode is still pending.
- On-demand live video remains intermittent: some sessions end with
  `source ended` and no frames, others deliver video after about 6 seconds.
- Plugin `npm test` and `npm run test:media` passed locally.
- Entrance opening from the listener was verified physically on the test
  installation: press and release each answered `200 Ok` in under 200 ms.

## 2026-10-04

### Added

- Dedicated Homebridge camera/doorbell plugin with local IPC, encrypted video
  transport, reception diagnostics and bounded source renewal.
- Incoming SIP dialog management, explicit answer/hangup and gated two-way audio.
- Opt-in contextual opening and a separate HomeKit lock accessory. Displayed
  lock state is assumed and resets after three seconds, without sensor feedback.
- Camera candidate discovery and privacy-preserving entrance-signaling diagnostics.
- macOS dedicated-account credential migration with backup and certificate/key
  checks; legacy credential files may omit GatewayId when SIP domains agree.
- Plugin lifecycle, lock, media transport and audio tests; plugin checks in CI.

### Fixed

- Listener installation now includes its new Python modules and restores them
  along with probe/wrapper files on installation failure.
- Updated the outbound-camera test fixture to supply the required SDP and
  signaling helpers.
- Expanded ignore rules for private provisioning output and Node dependencies.

### Documentation

- Updated current capabilities, architecture, onboarding and validation status.
- Documented individual obsolete-phone removal on the HOMETOUCH wall panel.
- Documented recovery after an empty HTTP 201 provisioning response.
- Marked earlier live-validation notes as historical and linked current status.

### Validation and remaining limits

- Python: 86 tests completed, one optional dependency test skipped locally.
- Plugin lifecycle/lock tests and local encrypted video/audio round trips passed.
- Dedicated-account certificate enrollment, installation and SIP registration
  verified on one installation. HomeKit video reception observed after migration.
- Freshness and sustained reliability of on-demand video need further checks.
  The observed on-demand session used diagnostic silence. Physical notification,
  conversation and correct-door opening after migration remain pending.
- HKSV recording and a general-purpose dedicated-plugin installer are not provided.

## Earlier published onboarding fixes

Before this update, commits `de4987c`, `4945442` and `5846c7a` added redacted HTTP
diagnostics, SIP password-availability reporting and recovery of an existing
endpoint without creating duplicates. The October update preserves these fixes.
