import asyncio
from pathlib import Path

from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA, async_validate_config_item
from homeassistant.components.blueprint import models
from homeassistant.util.yaml import load_yaml

BLUEPRINT = Path(__file__).resolve().parents[1] / "blueprints/automation/bticino_hometouch/ring_notification.yaml"


async def test_ring_blueprint_is_valid(hass):
    blueprint = models.Blueprint(load_yaml(BLUEPRINT), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    assert set(blueprint.inputs) >= {"doorbell", "entrance_sensor", "camera", "open_buttons", "notify_devices"}
    config = {
        "id": "ring_test",
        "use_blueprint": {
            "path": "bticino_hometouch/ring_notification.yaml",
            "input": {
                "doorbell": "event.videocitofono_campanello",
                "entrance_sensor": "sensor.videocitofono_ingresso_ultima_suonata",
                "camera": "camera.videocitofono_telecamera",
                "open_buttons": ["button.videocitofono_apri_scala", "button.videocitofono_apri_esterno"],
                "notify_devices": ["abc123"],
            },
        },
    }
    inputs = models.BlueprintInputs(blueprint, config)
    inputs.validate()
    validated = await async_validate_config_item(hass, "ring_test", inputs.async_substitute())
    assert validated is not None
    assert validated.validation_error is None, validated.validation_error


async def test_ring_blueprint_runs_notifies_and_opens(hass):
    """Run the automation end to end: ring -> notification -> button in it."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

    entry = MockConfigEntry(domain="mobile_app")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("mobile_app", "phone")}, name="Test Phone")
    notifications = async_mock_service(hass, "notify", "mobile_app_test_phone")
    presses = async_mock_service(hass, "button", "press")
    hass.states.async_set("sensor.videocitofono_ingresso_ultima_suonata", "not_recognized")
    hass.states.async_set("camera.videocitofono_telecamera", "idle")
    hass.states.async_set("button.videocitofono_apri_scala", "unknown", {"entrance": "scala", "friendly_name": "Apri scala"})
    hass.states.async_set("button.videocitofono_apri_esterno", "unknown", {"entrance": "esterno", "friendly_name": "Apri esterno"})
    hass.states.async_set("event.videocitofono_campanello", "unknown", {"event_type": None, "event_types": ["ring"]})

    blueprint = models.Blueprint(load_yaml(BLUEPRINT), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA)
    inputs = models.BlueprintInputs(blueprint, {"id": "ring_run", "use_blueprint": {
        "path": "bticino_hometouch/ring_notification.yaml",
        "input": {"doorbell": "event.videocitofono_campanello",
                  "entrance_sensor": "sensor.videocitofono_ingresso_ultima_suonata",
                  "camera": "camera.videocitofono_telecamera",
                  "open_buttons": ["button.videocitofono_apri_scala", "button.videocitofono_apri_esterno"],
                  "notify_devices": [device.id], "entrance_wait": 5}}})
    config = inputs.async_substitute()
    config.pop("use_blueprint", None)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()

    hass.states.async_set("event.videocitofono_campanello", "2026-10-06T15:51:46.904+00:00",
                          {"event_type": "ring", "event_types": ["ring"]})
    # Let the run reach its wait (block_till_done would also wait out its timeout).
    for _ in range(20):
        await asyncio.sleep(0)
    hass.states.async_set("sensor.videocitofono_ingresso_ultima_suonata", "scala")
    for _ in range(50):
        await asyncio.sleep(0)

    assert len(notifications) == 1, "the ring must send one notification"
    data = notifications[0].data["data"]
    assert data["tag"].startswith("BTICINO_")
    assert data["push"] == {"sound": "default", "interruption-level": "time-sensitive"}
    assert data["url"] == "entityId:camera.videocitofono_telecamera"
    assert [a["title"] for a in data["actions"]] == ["Apri scala"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": data["actions"][0]["action"]})
    await hass.async_block_till_done()
    assert [c.data["entity_id"] for c in presses] == [["button.videocitofono_apri_scala"]]
