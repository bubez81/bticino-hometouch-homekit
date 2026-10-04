# Changelog

Changes are dated by publication. This project remains experimental; entries
describe implemented behavior separately from physical validation. No stable
release or npm publication is implied.

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
