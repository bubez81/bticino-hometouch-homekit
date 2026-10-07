import asyncio

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
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


async def test_config_flow_signs_in_and_sets_up_the_phone(hass, listener, tmp_path):
    from unittest.mock import AsyncMock, patch
    calls = []

    async def run_setup(command, email, password, *args):
        calls.append((command, email, args))
        assert password == "secret"
        if command == "plants":
            return {"ok": True, "plants": [{"id": "P1", "name": "Casa"}, {"id": "P2", "name": "Ufficio"}]}
        return {"ok": True, "plant": "Casa", "entrances": [{"name": "Ingresso", "address": "20"}]}

    hass.config.config_dir = str(tmp_path)
    # With http, the live view is set up too: its cleanup must not break unloading.
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "http", {})
    runtime = AsyncMock()
    runtime.socket = tmp_path / "hometouch.sock"
    with patch("custom_components.bticino_hometouch.config_flow.run_setup", run_setup), \
         patch("custom_components.bticino_hometouch.config_flow.prepare_api", return_value=(listener.port, TOKEN)), \
         patch("custom_components.bticino_hometouch._start_runtime", return_value=runtime):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["step_id"] == "user"
        plant = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "bridge@example.com", "password": "secret"})
        assert plant["step_id"] == "plant"
        done = await hass.config_entries.flow.async_configure(plant["flow_id"], {"plant": "P1"})
        assert done["type"] is FlowResultType.CREATE_ENTRY
        assert done["title"] == "Casa"
        assert done["data"][CONF_PORT] == listener.port and done["data"]["storage"].endswith("bticino_hometouch")
        assert done["options"]["entrances"] == [{"name": "Ingresso", "address": "20"}]
        assert calls[1][0] == "apply" and "--plant-id" in calls[1][2] and "P1" in calls[1][2]
        await hass.async_block_till_done()
        assert hass.states.async_entity_ids("event"), "doorbell entity created"
        entry = done["result"]
        assert hass.data[DOMAIN][entry.entry_id].socket_path == runtime.socket
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert entry.entry_id not in hass.data[DOMAIN]
    runtime.stop.assert_awaited()


async def test_config_flow_reports_sign_in_errors(hass):
    from unittest.mock import patch

    async def run_setup(command, email, password, *args):
        return {"ok": False, "error": "Credenziali non valide"}

    with patch("custom_components.bticino_hometouch.config_flow.run_setup", run_setup):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        bad = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"email": "bridge@example.com", "password": "nope"})
    assert bad["errors"] == {"base": "setup_failed"}


async def test_options_edit_entrances(hass, listener):
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1", CONF_PORT: listener.port, CONF_TOKEN: TOKEN},
                            options={"entrances": [{"name": "Scala", "address": "20"}]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["data_schema"]({})["entrances"] == "Scala=20"
    bad = await hass.config_entries.options.async_configure(form["flow_id"], {"entrances": "Scala=venti"})
    assert bad["errors"] == {"entrances": "invalid_entrances"}
    ok = await hass.config_entries.options.async_configure(bad["flow_id"], {"entrances": "Scala=20, Esterno=21"})
    assert ok["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["entrances"][1] == {"name": "Esterno", "address": "21"}
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)


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
    assert hass.states.get("sensor.videocitofono_ingresso_ultima_suonata").state == "not_recognized"

    listener.bus.publish("entrance_detected", call="abc123", entrance="scala")
    await wait_for(lambda: hass.states.get("sensor.videocitofono_ingresso_ultima_suonata").state == "scala")
    listener.bus.publish("call_ended", call="abc123", reason="CANCEL ricevuto")
    await wait_for(lambda: hass.states.get("binary_sensor.videocitofono_chiamata_in_corso").state == "off")
    assert [e.data["type"] for e in bus_events][:4] == ["sip_registered", "ring", "entrance_detected", "call_ended"]
    assert bus_events[2].data["entrance"] == "scala"

    await hass.services.async_call("button", "press", {"entity_id": "button.videocitofono_apri_scala"}, blocking=True)
    assert listener.commands == [{"command": "open_entrance", "entrance": "scala"}]

    from homeassistant.components.camera import CameraEntityFeature, async_get_image, async_get_stream_source
    camera_state = hass.states.get("camera.videocitofono_telecamera")
    assert camera_state.attributes["supported_features"] & CameraEntityFeature.STREAM
    assert await async_get_stream_source(hass, "camera.videocitofono_telecamera") == \
        "rtsp://user:pw@198.51.100.2:8554/videocitofono"
    image = await async_get_image(hass, "camera.videocitofono_telecamera", width=640, height=360)
    assert image.content == b"\xff\xd8640x360"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_buttons_of_removed_entrances_are_cleaned_up(hass, listener):
    from homeassistant.helpers import entity_registry as er
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "127.0.0.1", CONF_PORT: listener.port, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create("button", DOMAIN, f"{entry.entry_id}_open_ingresso", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    buttons = lambda: sorted(e.unique_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)
                             if e.domain == "button")
    await wait_for(lambda: len(buttons()) == 2)
    assert buttons() == [f"{entry.entry_id}_open_esterno", f"{entry.entry_id}_open_scala"]
    assert registry.async_get(old.entity_id) is None
    await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_refreshes_the_phone_credentials(hass, listener, tmp_path):
    from unittest.mock import AsyncMock, patch
    calls = []

    async def run_setup(command, email, password, *args):
        calls.append((command, email, password, args))
        return {"ok": True, "updated": ["Username"]}

    entry = MockConfigEntry(domain=DOMAIN, data={"storage": str(tmp_path), CONF_HOST: "127.0.0.1",
                                                 CONF_PORT: listener.port, CONF_TOKEN: TOKEN})
    entry.add_to_hass(hass)
    with patch("custom_components.bticino_hometouch._start_runtime", return_value=AsyncMock()), \
         patch("custom_components.bticino_hometouch.config_flow.run_setup", run_setup):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        form = await entry.start_reconfigure_flow(hass)
        assert form["step_id"] == "reconfigure"
        done = await hass.config_entries.flow.async_configure(
            form["flow_id"], {"email": "bridge@example.com", "password": "secret"})
        await hass.async_block_till_done()
    assert done["type"] is FlowResultType.ABORT and done["reason"] == "credentials_refreshed"
    assert calls == [("refresh", "bridge@example.com", "secret", ("--storage", str(tmp_path)))]
    await hass.config_entries.async_unload(entry.entry_id)
