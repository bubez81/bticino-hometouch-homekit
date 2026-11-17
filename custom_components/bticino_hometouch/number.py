"""Ringer volume (optimistic, experimental).

Write-only: there is no read-back from the gateway, and the OpenWebNet volume
code mapping is install-specific, so the value starts unknown and is a best-effort
control that may need tuning (ringer_volume_codes on the listener side).
"""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .api import ApiError
from .entity import HometouchEntity
from .switch import RINGER_ERRORS


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    if "ringer_volume" not in hub.info.get("capabilities", []):
        return
    async_add_entities([HometouchRingerVolume(hub)])


class HometouchRingerVolume(HometouchEntity, NumberEntity):
    _attr_translation_key = "ringer_volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hub) -> None:
        super().__init__(hub, "ringer_volume")
        self._attr_native_value = None  # unknown until first set (no read-back)

    async def async_set_native_value(self, value: float) -> None:
        level = max(0, min(100, int(round(value))))
        try:
            await self.hub.api.set_ringer("volume", level)
        except ApiError as err:
            raise HomeAssistantError(
                f"Volume suoneria non riuscito: {RINGER_ERRORS.get(str(err), str(err))}") from err
        self._attr_native_value = level
        self.async_write_ha_state()
