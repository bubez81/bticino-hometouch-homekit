"""Camera entity: snapshots from the listener and, when configured, live RTSP video."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
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
        # Bundled listener: local MPEG-TS; external listener: its go2rtc RTSP URL.
        self._live_url = hub.live_url or hub.info.get("live_rtsp_url")
        if self._live_url:
            # Home Assistant's built-in go2rtc turns this RTSP source into WebRTC.
            self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        # Read by the BTicino HOMETOUCH dashboard card to open the talk channel.
        return {"talk_url": self.hub.talk_url} if self.hub.talk_url else None

    async def stream_source(self) -> str | None:
        return self._live_url

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            return await self.hub.api.snapshot(width, height)
        except ApiError:
            return None
