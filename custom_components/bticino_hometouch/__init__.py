"""BTicino HOMETOUCH: doorbell, camera and entrance opening.

The integration runs the bundled listener (its own phone of the HOMETOUCH
system) and talks to it through the listener's local API. Entries made with
older versions connect to an external listener instead.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, HometouchApi
from .const import CONF_ENTRANCES, CONF_GATEWAY, CONF_STORAGE, DOMAIN
from .live import LiveSource, LiveView
from .hub import HometouchHub
from .runtime import ListenerRuntime, SetupError, ensure_ffmpeg

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CAMERA,
             Platform.EVENT, Platform.IMAGE, Platform.SENSOR]

type HometouchConfigEntry = ConfigEntry[HometouchHub]


def entrance_ids(entrances: list[dict[str, str]]) -> dict[str, str]:
    """Listener ids (^[a-z0-9_-]{1,32}$) from the entrance names, as the Homebridge plugin does."""
    result: dict[str, str] = {}
    for entrance in entrances:
        base = unicodedata.normalize("NFD", entrance["name"]).encode("ascii", "ignore").decode().lower()
        base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")[:28] or "entrance"
        name, n = base, 2
        while name in result:
            name, n = f"{base}_{n}", n + 1
        result[name] = entrance["address"]
    return result


async def _start_runtime(hass: HomeAssistant, entry: HometouchConfigEntry) -> ListenerRuntime:
    storage = Path(entry.data[CONF_STORAGE])
    try:
        ffmpeg = await hass.async_add_executor_job(ensure_ffmpeg, storage)
    except (SetupError, OSError) as err:
        raise ConfigEntryNotReady(f"FFmpeg: {err}") from err
    runtime = ListenerRuntime(storage, ffmpeg, entrance_ids(entry.options.get(CONF_ENTRANCES, [])),
                              entry.data[CONF_PORT], gateway=entry.options.get(CONF_GATEWAY) or None)
    await runtime.start()
    return runtime


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry) -> bool:
    runtime = await _start_runtime(hass, entry) if CONF_STORAGE in entry.data else None
    api = HometouchApi(async_get_clientsession(hass), entry.data[CONF_HOST],
                       entry.data[CONF_PORT], entry.data[CONF_TOKEN])
    try:
        info = None
        # The bundled listener needs a few seconds to start its API.
        for attempt in range(30 if runtime else 1):
            try:
                info = await api.info()
                break
            except ApiError:
                if not runtime or attempt == 29:
                    raise
                await asyncio.sleep(1)
        hub = HometouchHub(hass, api, info, entry.entry_id)
        await hub.async_start()
    except AuthError as err:
        if runtime:
            await runtime.stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except ApiError as err:
        if runtime:
            await runtime.stop()
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    entry.async_on_unload(hub.async_stop)
    if runtime:
        entry.async_on_unload(runtime.stop)
    if runtime and hass.http is not None:
        # Live video through Home Assistant's built-in go2rtc (local HTTP MPEG-TS).
        sources = hass.data.setdefault(DOMAIN, {})
        if not sources.get("_view"):
            hass.http.register_view(LiveView())
            sources["_view"] = True
        source = sources[entry.entry_id] = LiveSource(runtime.socket)
        hub.live_url = source.url(hass, entry.entry_id)

        @callback
        def _forget_source() -> None:
            sources.pop(entry.entry_id, None)

        entry.async_on_unload(_forget_source)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _options_updated(hass: HomeAssistant, entry: HometouchConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HometouchConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
