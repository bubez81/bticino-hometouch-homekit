"""Ringer controls: optimistic switches and volume number map to listener commands."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.bticino_hometouch.api import ApiError
from custom_components.bticino_hometouch.number import HometouchRingerVolume
from custom_components.bticino_hometouch.switch import HometouchRingerSwitch


def _hub():
    hub = MagicMock()
    hub.entry_id, hub.info = "entry1", {}
    return hub


async def test_mute_switch_sends_command(hass):
    hub = _hub()
    switch = HometouchRingerSwitch(hub, "mute", "ringer_mute", "mdi:bell-off")
    switch.async_write_ha_state = lambda: None
    hub.api.set_ringer = AsyncMock(return_value={"ok": True})
    await switch.async_turn_on()
    hub.api.set_ringer.assert_awaited_with("mute", True)
    assert switch.is_on is True
    await switch.async_turn_off()
    hub.api.set_ringer.assert_awaited_with("mute", False)
    assert switch.is_on is False


async def test_switch_failure_is_reported_and_state_unchanged(hass):
    hub = _hub()
    switch = HometouchRingerSwitch(hub, "voicemail", "ringer_voicemail", "mdi:voicemail")
    switch.async_write_ha_state = lambda: None
    hub.api.set_ringer = AsyncMock(side_effect=ApiError("sip_unavailable"))
    with pytest.raises(HomeAssistantError, match="collegato"):
        await switch.async_turn_on()
    assert switch.is_on is False


async def test_volume_number_sends_rounded_level(hass):
    hub = _hub()
    number = HometouchRingerVolume(hub)
    number.async_write_ha_state = lambda: None
    hub.api.set_ringer = AsyncMock(return_value={"ok": True})
    assert number.native_value is None  # unknown until first set (no read-back)
    await number.async_set_native_value(42.6)
    hub.api.set_ringer.assert_awaited_with("volume", 43)
    assert number.native_value == 43
