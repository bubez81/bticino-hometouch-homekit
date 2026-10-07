![BTicino HOMETOUCH HomeKit bridge — privacy-first local intercom integration](https://raw.githubusercontent.com/bubez81/bticino-hometouch-homekit/main/assets/project-banner.png)

# homebridge-bticino-hometouch

[![Tests](https://github.com/bubez81/bticino-hometouch-homekit/actions/workflows/tests.yml/badge.svg)](https://github.com/bubez81/bticino-hometouch-homekit/actions/workflows/tests.yml)
[![npm](https://img.shields.io/npm/v/homebridge-bticino-hometouch?logo=npm&color=CB3837)](https://www.npmjs.com/package/homebridge-bticino-hometouch)
[![Homebridge](https://img.shields.io/badge/Homebridge-1.6%20%7C%202.x-491F59?logo=homebridge&logoColor=white)](https://homebridge.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/bubez81/bticino-hometouch-homekit/blob/main/LICENSE)
[![Status: experimental](https://img.shields.io/badge/status-experimental-orange.svg)](https://github.com/bubez81/bticino-hometouch-homekit)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/bubez81)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy_Me_a_Coffee-support-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/bubez81)

BTicino HOMETOUCH video door entry in Apple Home, with one plugin:

- ring notifications with the visitor's picture;
- live view with the entrance panel's sound, and **two-way audio**: answer a
  ring from the notification and talk to the door;
- **one lock per entrance** inside the doorbell, so the ring notification
  offers the gates;
- optional connection for the Home Assistant integration in the same
  repository.

The plugin runs the SIP listener itself (bundled, written in Python) and uses
FFmpeg from `ffmpeg-for-homebridge`, which includes the Speex codec the
HOMETOUCH gateway needs. No other service to install.

> Experimental, verified on one HOMETOUCH installation. Not affiliated with
> BTicino or Legrand.

## You need

- A BTicino **HOMETOUCH** system and a **dedicated Door Entry account**: create
  a new account with its own email address and have the system owner invite it
  in the Door Entry app (accept the invitation once). Do not use your personal
  account: the plugin registers the bridge as one more phone of the system.
- Homebridge 1.6 or newer (Raspberry Pi image, macOS or Linux) with **Python 3**
  (already included in the Homebridge Raspberry Pi image) and internet access
  the first time.
- A free phone slot on the system (HOMETOUCH accepts up to about 20).

## Install in three steps

1. Homebridge → **Plugins** → search **BTicino HOMETOUCH** → *Install*.
2. Open the plugin **settings**, sign in with the dedicated account, choose the
   system and press **Configura**. The entrance panel's gate is added
   automatically; rename it, add a second gate if you have one (address
   usually `21`) and use **Prova apertura** to check each one. Save.
3. Restart Homebridge. In Apple Home: **+** → *Add Accessory* → *More options*
   → **Videocitofono**, enter the code shown in the Homebridge log, choose
   **Stream** and enable doorbell notifications.

## Using it

The gates appear as locks inside the *Videocitofono* accessory, in its room,
so the ring notification offers them. Apple Home lists them under *Other*; to
show them as separate tiles: accessory settings → **Show as Separate Tiles**.

- **Ring:** notification on iPhone, iPad and Apple Watch; HomePods chime. Open
  it for the live view; the talk button speaks to the door. An answered call
  lasts until the panel ends it (about a minute).
- **Live view without a ring:** opens a camera call with sound; the panel ends
  it after about a minute and the plugin opens a new one.
- **Gates:** each lock opens its gate for a few seconds and returns to locked
  (there is no lock sensor).
- **Home Assistant:** enable *Home Assistant* in the settings; the log shows
  where the access token is. Then install the `bticino_hometouch` integration
  from HACS with the Homebridge host, port `8790` and that token.

## Updating and removing

- **Update:** Homebridge → Plugins → *Update*. The connection to the system,
  the gates and the pairing in Apple Home are kept.
- **Remove:** uninstall the plugin, then delete the Homebridge storage folder
  `bticino-hometouch` and remove *Videocitofono* from Apple Home. The bridge's
  phone stays registered on the system until the owner removes it in the Door
  Entry app.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| "not set up yet" in the log | Complete step 2 |
| Accessory without picture or live | Wait about 6 s; after many live views in a row the gateway needs 10–20 minutes of rest |
| Live view without sound | Check the `BTicino live audio` lines in the log (sound level of the panel) |
| A gate does not open | Check its address with **Prova apertura** |
| No notification | In Apple Home, accessory settings → doorbell notifications on |
| Home Assistant offers a "BTicino HOMETOUCH" bridge to pair | It is Homebridge's own (empty) bridge: ignore it |
| Anything else | The Homebridge log shows the listener's lines with the `[listener]` prefix; calls are logged in `bticino-hometouch/camera-calls.log` |

Private files (SIP certificate, key, keys of calls) stay in
`<Homebridge storage>/bticino-hometouch` with owner-only permissions. Full
documentation, Home Assistant integration and source:
<https://github.com/bubez81/bticino-hometouch-homekit>.

## Support the project

If this project is useful to you, you can support its continued development,
testing and documentation through
[GitHub Sponsors](https://github.com/sponsors/bubez81) or
[Buy Me a Coffee](https://buymeacoffee.com/bubez81). Contributions are
optional and do not include rewards or support services.

## License

MIT
