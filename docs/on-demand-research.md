# On-demand video research

Current status: 2026-10-04. Outbound probing and dedicated-plugin integration
are implemented experimentally. End-to-end freshness and sustained reliability
are not yet established across installations.

Static inspection of the official Android client identified two different paths:

- `VctLinphoneService.P` sends doorlock/service MESSAGE commands for CIDs
  10060/3008 and 2009. These must not be used as camera activation probes.
- `VctHomepageFragment` (`y0/n.s0`) starts a call with `DEVADDR` formed by
  concatenating device type and address. CID 10061 also adds `TVCC=1`.
  `CallManager` adds these as custom session SDP attributes and invokes
  `inviteAddressWithParams`, with video direction RecvOnly.

The configuration reader extracts explicit candidates (CIDs 10050/10061) and
the configured default camera. It preserves leading zeroes, skips invalid or
unknown addresses, and never assigns entrance names from list order. This is
discovery only; `scripts/probe-camera.py` implements the separate outbound SIP
transaction and media handling.

To save the small private candidate file outside the repository:

```sh
.venv/bin/python src/bticino_onboard.py --export-camera-candidates
```

The default destination is the user's configuration directory under
`bticino-hometouch/camera-candidates.json`, with mode 600. Do not share this file.
The command neither creates SIP endpoints nor sends camera/doorlock commands.
The probe handles outbound negotiation, ACK and termination. Tests exercise the
signaling lifecycle with synthetic responses. Hardware call lifetime remains
gateway-controlled; compare visible movement with the real scene to verify freshness.

The packaged `homekit-live-wrapper.sh` is an experimental standalone probe. It
must not be assigned as the global `videoProcessor` for a Homebridge platform,
because that would affect every Camera-ffmpeg accessory. The dedicated plugin
uses its own stream manager and local IPC instead. Outgoing preview audio is
currently diagnostic silence; incoming-call two-way audio is a separate path.
