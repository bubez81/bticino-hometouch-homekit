"""One button per configured entrance: sends the opening pulse."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .api import ApiError
from .entity import HometouchEntity


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    if hub.info.get("opening_enabled"):
        async_add_entities(HometouchOpenButton(hub, name) for name in hub.entrances)


class HometouchOpenButton(HometouchEntity, ButtonEntity):
    _attr_translation_key = "open_entrance"
    _attr_icon = "mdi:gate-open"

    def __init__(self, hub, entrance: str) -> None:
        super().__init__(hub, f"open_{entrance}")
        self.entrance = entrance
        self._attr_translation_placeholders = {"entrance": entrance.replace("_", " ").title()}
        # Lets automations and the ring blueprint match the button to the detected entrance.
        self._attr_extra_state_attributes = {"entrance": entrance}

    async def async_press(self) -> None:
        try:
            result = await self.hub.api.open_entrance(self.entrance)
        except ApiError as err:
            raise HomeAssistantError(f"Apertura non inviata: {err}") from err
        if not result.get("ok"):
            raise HomeAssistantError(f"Apertura rifiutata: {result.get('error')}")
