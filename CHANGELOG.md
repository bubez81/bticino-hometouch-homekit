# Changelog

Changes are dated by publication. This project remains experimental; entries
describe implemented behavior separately from physical validation. No stable
release or npm publication is implied.

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

### Changed

- `config.schema.json` accepts `standalone`, `serialNumber`, `ffmpegPath` and
  `audioFfmpegPath`; the last two were already used by the plugin.
- `config.child.example.json` enables `standalone`.

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
