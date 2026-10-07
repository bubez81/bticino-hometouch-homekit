![BTicino HOMETOUCH HomeKit bridge — privacy-first local intercom integration](assets/project-banner.png)

# BTicino HOMETOUCH for Apple Home and Home Assistant

[![Tests](https://github.com/bubez81/bticino-hometouch-homekit/actions/workflows/tests.yml/badge.svg)](https://github.com/bubez81/bticino-hometouch-homekit/actions/workflows/tests.yml)
[![npm](https://img.shields.io/npm/v/homebridge-bticino-hometouch?logo=npm&color=CB3837)](https://www.npmjs.com/package/homebridge-bticino-hometouch)
[![Homebridge](https://img.shields.io/badge/Homebridge-1.6%20%7C%202.x-491F59?logo=homebridge&logoColor=white)](https://homebridge.io)
[![HACS: custom](https://img.shields.io/badge/HACS-custom-41BDF5?logo=homeassistantcommunitystore&logoColor=white)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home_Assistant-2026.7%2B-18BCF2?logo=homeassistant&logoColor=white)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/bubez81/bticino-hometouch-homekit/blob/main/LICENSE)
[![Status: experimental](https://img.shields.io/badge/status-experimental-orange.svg)](https://github.com/bubez81/bticino-hometouch-homekit)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/bubez81)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy_Me_a_Coffee-support-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/bubez81)

Use a BTicino **HOMETOUCH** video door entry system from Apple Home, from
Home Assistant, or from both: ring notifications, live view with sound, and
opening the gates. Pick what you use at home:

| | **Apple Home** | **Home Assistant** |
| --- | --- | --- |
| What to install | the Homebridge plugin `homebridge-bticino-hometouch` | the `bticino_hometouch` integration from HACS |
| Ring notification | ✅ doorbell notification on iPhone, iPad, Mac, Watch | ✅ event + notification blueprint (picture, button for the gate that rang) |
| Live view with sound | ✅ | ✅ (through Home Assistant's built-in go2rtc) |
| Talk to the door | ✅ | not yet |
| Open the gates | ✅ as locks | ✅ as buttons |
| Guide | **[Plugin guide](homebridge-bticino-hometouch/README.md)** | **[Home Assistant guide](docs/home-assistant.md)** |

Each one works on its own; using both is fine (each registers as one more
phone of the system).

### Home Assistant in short

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/bubez81/bticino-hometouch-homekit` as **Integration**,
   or use the button below; install **BTicino HOMETOUCH** and restart Home
   Assistant.

   [![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bubez81&repository=bticino-hometouch-homekit&category=integration)

2. *Settings → Devices & services → Add integration → BTicino HOMETOUCH*:
   sign in with a **dedicated Door Entry account** invited to your system.
3. *Configure*: name and address of each gate (for example `Scala=20,
   Esterno=21`).
4. Ring once: the gateway at home is found automatically and the live view
   starts working.

Details, the notification blueprint and troubleshooting are in the
[Home Assistant guide](docs/home-assistant.md).

> Experimental, verified on one HOMETOUCH installation. Not affiliated with
> BTicino or Legrand. Use it only with your own system.

## How it works

The plugin and the integration register with the HOMETOUCH gateway as one
more phone of the system, using a dedicated Door Entry account. When someone
rings, the gateway calls them like the official app: they notify Apple Home or
Home Assistant and show the call's video; in Apple Home, answering from the
notification carries audio in both directions. Without a ring they call the
entrance panel's camera for the live view, and they open a gate with the same
command as the app.

Both run the same bundled SIP listener (Python) as a child process: inside
Homebridge with FFmpeg from `ffmpeg-for-homebridge`, inside Home Assistant
with a static FFmpeg from the same project (downloaded once and checked by its
SHA-256, because Home Assistant's own FFmpeg lacks the Speex codec).

## Repository

| Path | Content |
| --- | --- |
| `homebridge-bticino-hometouch/` | the Homebridge plugin (npm package) |
| `src/` | the SIP listener, call audio and setup helper bundled into the plugin |
| `scripts/probe-camera.py` | camera call for the live view, bundled into the plugin |
| `custom_components/bticino_hometouch/` | the Home Assistant integration (HACS); `listener/` is a copy of `src/` kept in sync by `tools/sync_listener.py` |
| `blueprints/` | Home Assistant blueprint for the ring notification |
| `tests/`, `tests_ha/` | Python and Home Assistant tests |

## Development

```sh
python3 -m unittest discover -s tests          # listener, audio, setup
python3 tools/sync_listener.py                 # after changing src/: refresh the HA copy
pip install pytest-homeassistant-custom-component PyTurboJPEG && python -m pytest tests_ha -q
cd homebridge-bticino-hometouch && npm install && npm test && npm run test:media
```

`npm pack` bundles `src/` and the camera probe into the package
(`bundle-listener.js`).

## Support the project

If this project is useful to you, you can support its continued development,
testing and documentation through
[GitHub Sponsors](https://github.com/sponsors/bubez81) or
[Buy Me a Coffee](https://buymeacoffee.com/bubez81). Contributions are
optional and do not include rewards or support services.

## Security and license

Credentials, certificates, call keys, logs and snapshots stay in the plugin's or
the integration's private folder and must never be published; see [SECURITY.md](SECURITY.md).
Changes are listed in the [changelog](CHANGELOG.md). MIT license.
