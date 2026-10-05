"""Image entity: the visitor snapshot of the most recent ring."""

from __future__ import annotations

from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HometouchConfigEntry
from .api import ApiError
from .entity import HometouchEntity


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([HometouchLastVisitor(hass, entry.runtime_data)])


class HometouchLastVisitor(HometouchEntity, ImageEntity):
    _attr_translation_key = "last_visitor"
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, hub) -> None:
        HometouchEntity.__init__(self, hub, "last_visitor")
        ImageEntity.__init__(self, hass)
        self._image: bytes | None = None
        ring = (hub.state or {}).get("last_ring") or {}
        self._attr_image_last_updated = dt_util.parse_datetime(ring["time"]) if ring.get("time") else None

    async def async_image(self) -> bytes | None:
        if self._image is None:
            try:
                self._image = await self.hub.api.snapshot()
            except ApiError:
                return None
        return self._image

    def _handle_hub_event(self, event: dict[str, Any] | None) -> None:
        if event and event.get("type") == "snapshot_ready":
            self._image = None
            self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()
