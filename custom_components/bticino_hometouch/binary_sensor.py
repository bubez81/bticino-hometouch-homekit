"""Binary sensors: SIP registration and call in progress."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .entity import HometouchEntity


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities([HometouchRegistered(hub), HometouchCallActive(hub)])


class HometouchRegistered(HometouchEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "sip_registered"

    def __init__(self, hub) -> None:
        super().__init__(hub, "sip_registered")

    @property
    def is_on(self) -> bool:
        return bool(self.hub.state.get("sip_registered"))


class HometouchCallActive(HometouchEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "call_active"

    def __init__(self, hub) -> None:
        super().__init__(hub, "call_active")

    @property
    def is_on(self) -> bool:
        return bool(self.hub.state.get("call_active"))
