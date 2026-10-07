# BTicino HOMETOUCH for Apple Home

Use a BTicino **HOMETOUCH** video door entry system in Apple Home through one
Homebridge plugin: ring notifications, live view with two-way audio, and the
gates as locks.

**→ Installation and use: [homebridge-bticino-hometouch](homebridge-bticino-hometouch/README.md)**

> Experimental, verified on one HOMETOUCH installation. Not affiliated with
> BTicino or Legrand. Use it only with your own system.

## How it works

The plugin registers with the HOMETOUCH gateway as one more phone of the
system, using a dedicated Door Entry account. When someone rings, the gateway
calls it like the official app: the plugin notifies Apple Home, shows the
call's video and, when you answer from the notification, carries audio in
both directions. Without a ring it calls the entrance panel's camera for the
live view, and it opens a gate with the same command as the app.

Everything runs inside Homebridge: the plugin starts its bundled SIP listener
(Python) and uses FFmpeg from `ffmpeg-for-homebridge`.

## Repository

| Path | Content |
| --- | --- |
| `homebridge-bticino-hometouch/` | the Homebridge plugin (npm package) |
| `src/` | the SIP listener, call audio and setup helper bundled into the plugin |
| `scripts/probe-camera.py` | camera call for the live view, bundled into the plugin |
| `custom_components/bticino_hometouch/` | Home Assistant integration (being reworked, see [docs/home-assistant.md](docs/home-assistant.md)) |
| `tests/`, `tests_ha/` | Python and Home Assistant tests |

## Development

```sh
python3 -m unittest discover -s tests          # listener, audio, setup
cd homebridge-bticino-hometouch && npm install && npm test && npm run test:media
```

`npm pack` bundles `src/` and the camera probe into the package
(`bundle-listener.js`).

## Security and license

Credentials, certificates, call keys, logs and snapshots stay in the plugin's
private folder and must never be published; see [SECURITY.md](SECURITY.md).
Changes are listed in the [changelog](CHANGELOG.md). MIT license.
