"""Camera entity: still images from the listener (live video in a later phase)."""

from __future__ import annotations

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .api import ApiError
from .entity import HometouchEntity


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([HometouchCamera(entry.runtime_data)])


class HometouchCamera(HometouchEntity, Camera):
    _attr_translation_key = "camera"

    def __init__(self, hub) -> None:
        HometouchEntity.__init__(self, hub, "camera")
        Camera.__init__(self)

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            return await self.hub.api.snapshot(width, height)
        except ApiError:
            return None
