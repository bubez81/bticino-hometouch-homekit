"""Sensors: time and entrance of the last ring."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HometouchConfigEntry
from .entity import HometouchEntity

UNKNOWN_ENTRANCE = "unknown"


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    async_add_entities([HometouchLastRing(hub), HometouchLastEntrance(hub)])


class HometouchLastRing(HometouchEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "last_ring"

    def __init__(self, hub) -> None:
        super().__init__(hub, "last_ring")

    @property
    def native_value(self) -> datetime | None:
        ring = self.hub.state.get("last_ring") or {}
        return dt_util.parse_datetime(ring["time"]) if ring.get("time") else None


class HometouchLastEntrance(HometouchEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "last_entrance"

    def __init__(self, hub) -> None:
        super().__init__(hub, "last_entrance")
        self._attr_options = [*hub.entrances, UNKNOWN_ENTRANCE]

    @property
    def native_value(self) -> str | None:
        ring = self.hub.state.get("last_ring")
        if not ring:
            return None
        entrance = ring.get("entrance")
        return entrance if entrance in self.hub.entrances else UNKNOWN_ENTRANCE
