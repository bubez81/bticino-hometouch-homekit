"""Keeps listener state in Home Assistant and follows its event stream."""

from __future__ import annotations

from pathlib import Path

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later

from .api import ApiError, AuthError, HometouchApi
from .const import BUS_EVENT, DOMAIN, RECONNECT_MAX_SECONDS, RECONNECT_MIN_SECONDS

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[dict[str, Any] | None], None]

# The HOMETOUCH can get stuck closing the phone's connection a few seconds
# after every registration (observed: every 16 s for a whole day, until it was
# restarted); rings and openings are then lost. A drop about every 17 minutes
# is normal, so the warning clears after 5 minutes without one.
UNSTABLE_DROPS = 6
UNSTABLE_WINDOW = 600
STABLE_AFTER = 300


class HometouchHub:
    """State holder; entities subscribe to updates and to raw events."""

    def __init__(self, hass: HomeAssistant, api: HometouchApi, info: dict[str, Any], entry_id: str) -> None:
        self.hass = hass
        self.api = api
        self.info = info
        self.entry_id = entry_id
        self.state: dict[str, Any] = {}
        self.connected = False
        self.snapshot_version = 0
        self.live_url: str | None = None
        self.talk_url: str | None = None
        # Second camera of the entrance panel (Tvcc): live URL and latest picture.
        self.second_live_url: str | None = None
        self.second_frame: Path | None = None
        # Per camera (0: the entrance panel's, 1: the next one): its live source and
        # the latest picture of its live views.
        self.sources: dict[int, Any] = {}
        self.frames: dict[int, Path] = {}
        self._listeners: list[Listener] = []
        self._task: asyncio.Task | None = None
        self.gateway_unstable = False
        self._drops: list[float] = []
        self._cancel_stable: Callable[[], None] | None = None

    @property
    def issue_id(self) -> str:
        return f"gateway_unstable_{self.entry_id}"

    @property
    def entrances(self) -> list[str]:
        return list(self.info.get("entrances") or [])

    @callback
    def add_listener(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self, event: dict[str, Any] | None) -> None:
        for listener in list(self._listeners):
            listener(event)

    async def async_start(self) -> None:
        # A warning left from before a restart comes back by itself if still true.
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        self.state = await self.api.state()
        self._task = self.hass.async_create_background_task(self._run(), f"{BUS_EVENT}_stream")

    async def async_stop(self) -> None:
        if self._cancel_stable:
            self._cancel_stable()
            self._cancel_stable = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        delay = RECONNECT_MIN_SECONDS
        while True:
            try:
                async for event in self.api.events():
                    if not self.connected:
                        self.connected = True
                        delay = RECONNECT_MIN_SECONDS
                        _LOGGER.info("Event stream connected")
                    self.handle_event(event)
            except AuthError as err:
                _LOGGER.error("Event stream rejected: %s", err)
                delay = RECONNECT_MAX_SECONDS
            except ApiError as err:
                _LOGGER.warning("Event stream lost: %s", err)
            if self.connected:
                self.connected = False
                self._notify(None)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    @callback
    def handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "state":
            self.state = {k: v for k, v in event.items() if k not in ("id", "type", "time")}
        elif kind == "sip_registered":
            self.state["sip_registered"] = True
        elif kind == "sip_disconnected":
            self.state["sip_registered"] = False
            self._connection_dropped()
        elif kind == "ring":
            self.state["call_active"] = True
            self.state["last_ring"] = {"time": event.get("time"), "call": event.get("call"), "entrance": None}
        elif kind == "entrance_detected":
            ring = self.state.get("last_ring")
            if ring and ring.get("call") == event.get("call"):
                ring["entrance"] = event.get("entrance")
        elif kind == "call_ended":
            self.state["call_active"] = False
        elif kind == "snapshot_ready":
            self.snapshot_version += 1
        elif kind == "entrance_open":
            self.state["last_entrance_open"] = {k: event.get(k) for k in ("time", "entrance", "result")}
        if kind != "state":
            self.hass.bus.async_fire(BUS_EVENT, {
                "entry_id": self.entry_id, "type": kind,
                **{k: v for k, v in event.items() if k in ("time", "call", "entrance", "reason", "result")},
            })
        self._notify(event)

    @callback
    def _connection_dropped(self) -> None:
        now = time.monotonic()
        self._drops = [t for t in self._drops if now - t < UNSTABLE_WINDOW] + [now]
        if self._cancel_stable:
            self._cancel_stable()
        self._cancel_stable = async_call_later(self.hass, STABLE_AFTER, self._connection_stable)
        if self.gateway_unstable or len(self._drops) < UNSTABLE_DROPS:
            return
        self.gateway_unstable = True
        _LOGGER.warning("The HOMETOUCH closed the connection %d times in %d minutes: restart it",
                        len(self._drops), UNSTABLE_WINDOW // 60)
        ir.async_create_issue(
            self.hass, DOMAIN, self.issue_id, is_fixable=False,
            severity=ir.IssueSeverity.ERROR, translation_key="gateway_unstable",
            translation_placeholders={"drops": str(len(self._drops)), "minutes": str(UNSTABLE_WINDOW // 60)},
        )
        self.hass.bus.async_fire(BUS_EVENT, {"entry_id": self.entry_id, "type": "gateway_unstable"})

    @callback
    def _connection_stable(self, _now: Any) -> None:
        self._cancel_stable = None
        self._drops.clear()
        if not self.gateway_unstable:
            return
        self.gateway_unstable = False
        _LOGGER.info("Connection to the HOMETOUCH stable again")
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        self.hass.bus.async_fire(BUS_EVENT, {"entry_id": self.entry_id, "type": "gateway_stable"})

    async def async_refresh_picture(self, camera: int) -> str:
        """A new picture of `camera` from a short call; returns why not, or ""."""
        source, frame = self.sources.get(camera), self.frames.get(camera)
        if source is None or frame is None:
            return "not_available"
        reason = await source.capture(frame)
        if not reason:
            self._notify({"type": "picture_updated", "camera": camera})
        return reason
