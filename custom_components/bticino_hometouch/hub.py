"""Keeps listener state in Home Assistant and follows its event stream."""

from __future__ import annotations

from pathlib import Path

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .api import ApiError, AuthError, HometouchApi
from .const import BUS_EVENT, RECONNECT_MAX_SECONDS, RECONNECT_MIN_SECONDS

_LOGGER = logging.getLogger(__name__)

Listener = Callable[[dict[str, Any] | None], None]


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
        self._listeners: list[Listener] = []
        self._task: asyncio.Task | None = None

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
        self.state = await self.api.state()
        self._task = self.hass.async_create_background_task(self._run(), f"{BUS_EVENT}_stream")

    async def async_stop(self) -> None:
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
