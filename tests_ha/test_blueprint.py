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
