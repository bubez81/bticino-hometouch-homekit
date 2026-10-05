"""BTicino HOMETOUCH: doorbell, camera and entrance opening via the local listener."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, HometouchApi
from .hub import HometouchHub

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.CAMERA,
             Platform.EVENT, Platform.IMAGE, Platform.SENSOR]

type HometouchConfigEntry = ConfigEntry[HometouchHub]


async def async_setup_entry(hass: HomeAssistant, entry: HometouchConfigEntry) -> bool:
    api = HometouchApi(async_get_clientsession(hass), entry.data[CONF_HOST],
                       entry.data[CONF_PORT], entry.data[CONF_TOKEN])
    try:
        info = await api.info()
        hub = HometouchHub(hass, api, info, entry.entry_id)
        await hub.async_start()
    except AuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ApiError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    entry.async_on_unload(hub.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HometouchConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
