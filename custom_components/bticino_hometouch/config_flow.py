"""Config flow: listener address and API token, with reauthentication."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, HometouchApi
from .const import DEFAULT_PORT, DOMAIN

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1, max=65535)),
    vol.Required(CONF_TOKEN): str,
})


class HometouchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, host: str, port: int, token: str) -> tuple[dict[str, Any] | None, str | None]:
        api = HometouchApi(async_get_clientsession(self.hass), host, port, token)
        try:
            return await api.info(), None
        except AuthError:
            return None, "invalid_auth"
        except ApiError:
            return None, "cannot_connect"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()
            info, error = await self._validate(user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_TOKEN])
            if error:
                errors["base"] = error
            elif info.get("api_version") != 1:
                errors["base"] = "unsupported_version"
            else:
                return self.async_create_entry(title="BTicino HOMETOUCH", data=user_input)
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(
            USER_SCHEMA, user_input or {}), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            _, error = await self._validate(entry.data[CONF_HOST], entry.data[CONF_PORT], user_input[CONF_TOKEN])
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(step_id="reauth_confirm",
                                    data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}), errors=errors)
