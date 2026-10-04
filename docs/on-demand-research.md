# On-demand video research

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
preparatory discovery, not a working outbound SIP implementation.

To save the small private candidate file outside the repository:

```sh
.venv/bin/python src/bticino_onboard.py --export-camera-candidates
```

The default destination is the user's configuration directory under
`bticino-hometouch/camera-candidates.json`, with mode 600. Do not share this file.
The command neither creates SIP endpoints nor sends camera/doorlock commands.
Next validation requires these real addresses and an outbound INVITE transaction
with authentication, media negotiation, ACK and clean termination.

The packaged `homekit-live-wrapper.sh` is an experimental standalone probe. It
must not be assigned as the global `videoProcessor` for a Homebridge platform,
because that would affect every Camera-ffmpeg accessory. A future integration
must attach it only to the dedicated doorbell stream after testing the plugin's
per-camera process model.
