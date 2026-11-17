"""Ringer mute and answering-machine switches.

Optimistic / write-only: the gateway accepts the command but never reports the
stored value back, so the shown state is the last value we set and can drift if
the setting is changed on the HOMETOUCH panel itself.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .api import ApiError
from .entity import HometouchEntity

RINGER_ERRORS = {
    "ringer_disabled": "i controlli suoneria non sono abilitati",
    "sip_unavailable": "il HOMETOUCH non è collegato",
    "send_failed": "comando non inviato al HOMETOUCH",
    "invalid_value": "valore non valido",
    "unknown_control": "controllo non riconosciuto",
}


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    if "ringer" not in hub.info.get("capabilities", []):
        return
    async_add_entities([
        HometouchRingerSwitch(hub, "mute", "ringer_mute", "mdi:bell-off"),
        HometouchRingerSwitch(hub, "voicemail", "ringer_voicemail", "mdi:voicemail"),
    ])


class HometouchRingerSwitch(HometouchEntity, SwitchEntity):
    def __init__(self, hub, control: str, translation_key: str, icon: str) -> None:
        super().__init__(hub, translation_key)
        self.control = control
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_is_on = False  # optimistic default: audible / answering machine off

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, value: bool) -> None:
        try:
            await self.hub.api.set_ringer(self.control, value)
        except ApiError as err:
            raise HomeAssistantError(
                f"Comando suoneria non riuscito: {RINGER_ERRORS.get(str(err), str(err))}") from err
        self._attr_is_on = value
        self.async_write_ha_state()
