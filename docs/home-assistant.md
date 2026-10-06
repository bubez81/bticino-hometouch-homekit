# Home Assistant integration

`custom_components/bticino_hometouch` connects Home Assistant to the listener's
[network API](../README.md#network-api-for-home-automation-optional). The listener
keeps doing all SIP work; Home Assistant can run on another host.

Status (2026-10): experimental. Tested with Home Assistant 2026.9/2026.10 and the
automated test suite in `tests_ha/`; a real outdoor ring in Home Assistant is
still being validated.

## 1. Enable the API on the listener host

In the listener `config.json`:

```json
"api": {"enabled": true, "bind": "0.0.0.0", "port": 8790,
        "token_file": "/opt/bticino-sniffer/private/api_token",
        "allowed_clients": ["<Home Assistant IP>"]}
```

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | sudo tee /opt/bticino-sniffer/private/api_token >/dev/null
sudo chmod 600 /opt/bticino-sniffer/private/api_token
```

Restart the listener. Enable `entrance_open_enabled` and `entrances` as well if
Home Assistant should open doors.

## 2. Install the integration

HACS → ⋮ → *Custom repositories* → add
`https://github.com/bubez81/bticino-hometouch-homekit` as an **Integration**,
install **BTicino HOMETOUCH**, restart Home Assistant. Then *Settings → Devices &
services → Add integration → BTicino HOMETOUCH* and enter the listener host,
port (8790) and token. A wrong token or a client outside `allowed_clients` is
reported as an authentication error; a changed token can be updated through
*Reconfigure/Re-authenticate* without removing the entry.

## 3. Entities

| Entity | Purpose |
| --- | --- |
| `event.<device>_campanello` / `doorbell` | `doorbell` event entity; fires `ring` on every call |
| `camera.<device>_telecamera` | snapshots at the size Home Assistant asks for |
| `image.<device>_ultimo_visitatore` | visitor image of the latest ring |
| `button.<device>_apri_<entrance>` | opening pulse per entrance; attribute `entrance` |
| `sensor.<device>_ultima_suonata` | timestamp of the latest ring |
| `sensor.<device>_ingresso_ultima_suonata` | entrance of the latest ring (`not_recognized` until recognised) |
| `binary_sensor.<device>_chiamata_in_corso` | an entrance panel call is active |
| `binary_sensor.<device>_registrazione_sip` | the listener is registered with the gateway (diagnostic) |

Every listener event is also fired on the bus as `bticino_hometouch_event`
(`type`: `ring`, `entrance_detected`, `snapshot_ready`, `call_ended`,
`entrance_open`, `sip_registered`, `sip_disconnected`). For automations prefer the
`event.received` trigger on the doorbell entity.

Entities are unavailable while the event stream is disconnected; the
integration reconnects automatically with backoff.

## 4. Ring notification with the right gate

Import the blueprint
[`ring_notification.yaml`](../blueprints/automation/bticino_hometouch/ring_notification.yaml)
(*Settings → Automations & scenes → Blueprints → Import* with its GitHub URL).
On a ring it waits a few seconds for the entrance to be recognised, then sends a
time-sensitive notification with the snapshot to the selected Companion App
devices and a button that opens **only the entrance that rang**. If the entrance
is not recognised in time, every entrance is offered (up to four). The button in
the notification stays valid for a configurable time (default two minutes).

## 5. MQTT locks and HomeKit

Existing MQTT locks fed by `bticino_mqtt_bridge.py` keep working; the buttons
above are an alternative that does not need MQTT.

If you expose this doorbell to Apple Home through Home Assistant's HomeKit
Bridge, publish it as its own accessory: Apple Home sends ring notifications only
for accessories advertised with the *Video Doorbell* category, which bridged
accessories do not carry. The same limitation led the Homebridge plugin to its
`standalone` option.

## 6. Tests

```sh
pip install pytest-homeassistant-custom-component PyTurboJPEG
python -m pytest tests_ha -q
```

The tests start the real listener API module on loopback and drive the
integration in a test Home Assistant instance: config flow, entities, ring and
entrance events, opening and camera images. The blueprint is validated with Home
Assistant's own blueprint and automation schemas.
