"""Camera entity: snapshots from the listener and, when configured, live RTSP video."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HometouchConfigEntry
from .api import ApiError
from .const import CONF_SECOND_CAMERA, DEFAULT_SECOND_CAMERA
from .entity import HometouchEntity


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    hub = entry.runtime_data
    cameras: list[Camera] = [HometouchCamera(hub)]
    if hub.second_live_url:
        name = (entry.options.get(CONF_SECOND_CAMERA) or DEFAULT_SECOND_CAMERA).strip()[:64] or DEFAULT_SECOND_CAMERA
        cameras.append(HometouchSecondCamera(hub, name))
    async_add_entities(cameras)


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


class HometouchSecondCamera(HometouchEntity, Camera):
    """The entrance panel's next camera (Tvcc), reached like the app's camera arrow.

    Its picture is the latest frame of a live view of that camera: previews
    must not call it, or the gateway would be called for every refresh.
    """

    _attr_icon = "mdi:cctv"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, hub, name: str) -> None:
        HometouchEntity.__init__(self, hub, "camera_1")
        Camera.__init__(self)
        self._attr_name = name

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        # The card's talk channel works during either camera's live view.
        return {"talk_url": self.hub.talk_url, "camera_index": 1} if self.hub.talk_url else None

    async def stream_source(self) -> str | None:
        return self.hub.second_live_url

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        frame = self.hub.second_frame
        if frame is not None:
            try:
                return await self.hass.async_add_executor_job(frame.read_bytes)
            except OSError:
                pass
        try:
            return await self.hub.api.snapshot(width, height)
        except ApiError:
            return None
