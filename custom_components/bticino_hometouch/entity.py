"""Base entity bound to one listener."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, MODEL
from .hub import HometouchHub


class HometouchEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: HometouchHub, key: str) -> None:
        self.hub = hub
        self._attr_unique_id = f"{hub.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.entry_id)}, manufacturer=MANUFACTURER, model=MODEL,
            name="Videocitofono", sw_version=f"API v{hub.info.get('api_version', '?')}")

    @property
    def available(self) -> bool:
        return self.hub.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.hub.add_listener(self._handle_hub_event))

    def _handle_hub_event(self, event: dict[str, Any] | None) -> None:
        self.async_write_ha_state()
