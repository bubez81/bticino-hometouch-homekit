"""Sensors: time and entrance of the last ring, connection with the HOMETOUCH."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HometouchConfigEntry
from .entity import HometouchEntity
from .hub import CONNECTED, DISCONNECTED, UNSTABLE

NOT_RECOGNIZED = "not_recognized"


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    entities = [HometouchLastRing(hub), HometouchLastEntrance(hub), HometouchConnection(hub)]
    # Only a standalone entry has a local certificate to watch.
    if hub.storage is not None:
        entities.append(HometouchCertificateExpiry(hub))
    async_add_entities(entities)


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
        self._attr_options = [*hub.entrances, NOT_RECOGNIZED]

    @property
    def native_value(self) -> str | None:
        ring = self.hub.state.get("last_ring")
        if not ring:
            return None
        entrance = ring.get("entrance")
        return entrance if entrance in self.hub.entrances else NOT_RECOGNIZED


class HometouchCertificateExpiry(HometouchEntity, SensorEntity):
    """When the phone's client certificate expires. The expiry warning (Repairs)
    and the auto-renewal watch this same value."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "certificate_expiry"

    def __init__(self, hub) -> None:
        super().__init__(hub, "certificate_expiry")

    @property
    def available(self) -> bool:
        # Independent of the SIP connection: the certificate is a local file.
        return self.hub.certificate_not_after is not None

    @property
    def native_value(self) -> datetime | None:
        return self.hub.certificate_not_after


class HometouchConnection(HometouchEntity, SensorEntity):
    """Whether rings and openings can work now; unstable means: restart the HOMETOUCH."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_translation_key = "connection"
    _attr_options = [CONNECTED, UNSTABLE, DISCONNECTED]
    ICONS = {CONNECTED: "mdi:lan-connect", UNSTABLE: "mdi:lan-pending", DISCONNECTED: "mdi:lan-disconnect"}

    def __init__(self, hub) -> None:
        super().__init__(hub, "connection")

    @property
    def native_value(self) -> str:
        return self.hub.connection

    @property
    def icon(self) -> str:
        return self.ICONS[self.hub.connection]
