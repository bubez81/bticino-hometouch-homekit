"""Config flow: sign in with the dedicated Door Entry account and set up the
bridge's phone; the integration then runs the listener itself."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow, OptionsFlowWithReload
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, HometouchApi
from .const import CONF_ENTRANCES, CONF_GATEWAY, CONF_STORAGE, DOMAIN, STORAGE_DIR
from .runtime import LISTENER_DIR

CONF_PLANT = "plant"


def parse_entrances(text: str) -> list[dict[str, str]]:
    """"Scala=20, Esterno=21" → [{"name": "Scala", "address": "20"}, …]."""
    entrances = []
    for part in (text or "").split(","):
        name, _, address = part.partition("=")
        name, address = name.strip(), address.strip()
        if not name and not address:
            continue
        if not name or not address.isdigit() or len(address) > 4:
            raise ValueError(part.strip())
        entrances.append({"name": name[:64], "address": address})
    return entrances


def valid_host(value: str) -> bool:
    """An IP address or host name of the gateway at home, without port or scheme."""
    return bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value))


def format_entrances(entrances: list[dict[str, str]]) -> str:
    return ", ".join(f"{e['name']}={e['address']}" for e in entrances)


def free_port(start: int = 8791) -> int:
    for port in range(start, start + 200):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError("No free local port")


def prepare_api(storage: Path) -> tuple[int, str]:
    """Local API port and token for the integration ↔ listener connection."""
    token_file = storage / "private" / "api_token"
    token_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not token_file.exists():
        with os.fdopen(os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
    return free_port(), token_file.read_text(encoding="utf-8").strip()


async def run_setup(command: str, email: str, password: str, *args: str) -> dict[str, Any]:
    """The bundled setup helper, as a separate process; the password only in its environment."""
    env = {**os.environ, "PYTHONPATH": str(LISTENER_DIR),
           "BTICINO_DOORENTRY_EMAIL": email, "BTICINO_DOORENTRY_PASSWORD": password}
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(LISTENER_DIR / "bticino_plugin_setup.py"), command, *args,
        env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    stdout, _ = await asyncio.wait_for(process.communicate(), 180)
    try:
        return json.loads(stdout.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "Risposta non valida dal programma di configurazione"}


class HometouchConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._email = ""
        self._password = ""
        self._plants: list[dict[str, str]] = []

    @property
    def _storage(self) -> Path:
        return Path(self.hass.config.path(STORAGE_DIR))

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if await self.hass.async_add_executor_job((self._storage / "onboarding.json").exists):
            return await self.async_step_reuse()
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            self._email, self._password = user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD]
            result = await run_setup("plants", self._email, self._password)
            if not result.get("ok"):
                errors["base"] = "setup_failed"
                placeholders["error"] = result.get("error", "")
            elif not result["plants"]:
                errors["base"] = "no_plants"
            else:
                self._plants = result["plants"]
                if len(self._plants) == 1:
                    return await self._apply(self._plants[0]["id"])
                return await self.async_step_plant()
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_EMAIL, default=self._email): str,
            vol.Required(CONF_PASSWORD): str,
        }), errors=errors, description_placeholders=placeholders)

    async def async_step_plant(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._apply(user_input[CONF_PLANT])
        return self.async_show_form(step_id="plant", data_schema=vol.Schema({
            vol.Required(CONF_PLANT): vol.In({p["id"]: p["name"] for p in self._plants}),
        }))

    async def _apply(self, plant_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(plant_id)
        self._abort_if_unique_id_configured()
        result = await run_setup("apply", self._email, self._password,
                                 "--plant-id", plant_id, "--storage", str(self._storage),
                                 "--device-name", "Home Assistant BTicino")
        self._password = ""
        if not result.get("ok"):
            return self.async_abort(reason="setup_failed", description_placeholders={"error": result.get("error", "")})
        return await self._create(result.get("plant") or "BTicino HOMETOUCH", result.get("entrances") or [])

    async def async_step_reuse(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A previous setup is in the folder: use the same phone, do not create another."""
        if user_input is not None:
            return await self._create("BTicino HOMETOUCH", [])
        return self.async_show_form(step_id="reuse", data_schema=vol.Schema({}))

    async def _create(self, title: str, entrances: list[dict[str, str]]) -> ConfigFlowResult:
        port, token = await self.hass.async_add_executor_job(prepare_api, self._storage)
        return self.async_create_entry(title=title, data={
            CONF_STORAGE: str(self._storage), CONF_HOST: "127.0.0.1", CONF_PORT: port, CONF_TOKEN: token,
        }, options={CONF_ENTRANCES: entrances})

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-read this phone's credentials from the cloud; nothing is created."""
        entry = self._get_reconfigure_entry()
        if not entry.data.get(CONF_STORAGE):
            return self.async_abort(reason="not_standalone")
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            result = await run_setup("refresh", user_input[CONF_EMAIL].strip(), user_input[CONF_PASSWORD],
                                     "--storage", entry.data[CONF_STORAGE])
            if result.get("ok"):
                return self.async_update_reload_and_abort(entry, reason="credentials_refreshed")
            errors["base"] = "setup_failed"
            placeholders["error"] = result.get("error", "")
        return self.async_show_form(step_id="reconfigure", data_schema=vol.Schema({
            vol.Required(CONF_EMAIL): str,
            vol.Required(CONF_PASSWORD): str,
        }), errors=errors, description_placeholders=placeholders)

    # Entries made with older versions point to an external listener (host, port, token).
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            api = HometouchApi(async_get_clientsession(self.hass), entry.data[CONF_HOST], entry.data[CONF_PORT],
                               user_input[CONF_TOKEN])
            try:
                await api.info()
            except AuthError:
                errors["base"] = "invalid_auth"
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(step_id="reauth_confirm",
                                    data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HometouchOptionsFlow()


class HometouchOptionsFlow(OptionsFlowWithReload):
    """Entrances: names and lock addresses, e.g. "Scala=20, Esterno=21"."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = format_entrances(self.config_entry.options.get(CONF_ENTRANCES, []))
        gateway = self.config_entry.options.get(CONF_GATEWAY, "")
        if user_input is not None:
            gateway = (user_input.get(CONF_GATEWAY) or "").strip()
            try:
                entrances = parse_entrances(user_input[CONF_ENTRANCES])
            except ValueError:
                errors[CONF_ENTRANCES] = "invalid_entrances"
            if gateway and not valid_host(gateway):
                errors[CONF_GATEWAY] = "invalid_gateway"
            if not errors:
                return self.async_create_entry(data={CONF_ENTRANCES: entrances, CONF_GATEWAY: gateway})
        schema = {vol.Optional(CONF_ENTRANCES, default=current): str}
        if self.config_entry.data.get(CONF_STORAGE):
            schema[vol.Optional(CONF_GATEWAY, description={"suggested_value": gateway})] = str
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema), errors=errors)
