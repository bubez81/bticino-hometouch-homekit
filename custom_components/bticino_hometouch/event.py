"""Doorbell event entity: fires `ring` on every call from the entrance panel."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import DoorbellEventType, EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .entity import HometouchEntity


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([HometouchDoorbell(entry.runtime_data)])


class HometouchDoorbell(HometouchEntity, EventEntity):
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [DoorbellEventType.RING]
    _attr_translation_key = "doorbell"

    def __init__(self, hub) -> None:
        super().__init__(hub, "doorbell")

    def _handle_hub_event(self, event: dict[str, Any] | None) -> None:
        if event and event.get("type") == "ring":
            # The entrance is usually identified a few seconds later; see the
            # "last entrance" sensor and the bticino_hometouch_event bus event.
            self._trigger_event(DoorbellEventType.RING, {"call": event.get("call")})
        self.async_write_ha_state()
