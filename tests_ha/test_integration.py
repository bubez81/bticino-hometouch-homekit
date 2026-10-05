import asyncio

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.bticino_hometouch.const import BUS_EVENT, DOMAIN

from .conftest import TOKEN


async def wait_for(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.05)


async def test_config_flow(hass, listener):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    bad = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: listener.port, CONF_TOKEN: "wrong-token-0123456789abcdefgh"})
    assert bad["errors"] == {"base": "invalid_auth"}
    unreachable = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1, CONF_TOKEN: TOKEN})
    assert unreachable["errors"] == {"base": "cannot_connect"}
    ok = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: listener.port, CONF_TOKEN: TOKEN})
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    assert ok["data"][CONF_PORT] == listener.port
    await hass.config_entries.async_unload(ok["result"].entry_id)


async def test_entities_events_and_opening(hass, listener):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1", CONF_PORT: listener.port, CONF_TOKEN: TOKEN},
                            unique_id=f"127.0.0.1:{listener.port}")
    hass.config.language = "it"
    entry.add_to_hass(hass)
    bus_events = async_capture_events(hass, BUS_EVENT)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    doorbell = "event.videocitofono_campanello"
    await wait_for(lambda: hass.states.get(doorbell) and hass.states.get(doorbell).state != "unavailable")
    ids = set(hass.states.async_entity_ids())
    for entity_id in (doorbell, "button.videocitofono_apri_scala", "button.videocitofono_apri_esterno",
                      "camera.videocitofono_telecamera", "image.videocitofono_ultimo_visitatore",
                      "sensor.videocitofono_ultima_suonata", "sensor.videocitofono_ingresso_ultima_suonata",
                      "binary_sensor.videocitofono_registrazione_sip", "binary_sensor.videocitofono_chiamata_in_corso"):
        assert entity_id in ids, (entity_id, sorted(i for i in ids if "videocitofono" in i))

    listener.bus.publish("sip_registered")
    listener.bus.publish("ring", call="abc123")
    await wait_for(lambda: hass.states.get(doorbell).attributes.get("event_type") == "ring")
    assert hass.states.get("binary_sensor.videocitofono_chiamata_in_corso").state == "on"
    assert hass.states.get("binary_sensor.videocitofono_registrazione_sip").state == "on"
    assert hass.states.get("sensor.videocitofono_ingresso_ultima_suonata").state == "unknown"

    listener.bus.publish("entrance_detected", call="abc123", entrance="scala")
    await wait_for(lambda: hass.states.get("sensor.videocitofono_ingresso_ultima_suonata").state == "scala")
    listener.bus.publish("call_ended", call="abc123", reason="CANCEL ricevuto")
    await wait_for(lambda: hass.states.get("binary_sensor.videocitofono_chiamata_in_corso").state == "off")
    assert [e.data["type"] for e in bus_events][:4] == ["sip_registered", "ring", "entrance_detected", "call_ended"]
    assert bus_events[2].data["entrance"] == "scala"

    await hass.services.async_call("button", "press", {"entity_id": "button.videocitofono_apri_scala"}, blocking=True)
    assert listener.commands == [{"command": "open_entrance", "entrance": "scala"}]

    from homeassistant.components.camera import async_get_image
    image = await async_get_image(hass, "camera.videocitofono_telecamera", width=640, height=360)
    assert image.content == b"\xff\xd8640x360"

    assert await hass.config_entries.async_unload(entry.entry_id)
