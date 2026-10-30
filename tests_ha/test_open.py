"""Opening a gate: a clear reason when the HOMETOUCH did not take the command."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.bticino_hometouch.button import HometouchOpenButton


async def test_open_reports_why_it_failed(hass):
    hub = MagicMock()
    hub.entry_id, hub.info = "entry1", {}
    button = HometouchOpenButton(hub, "esterno")
    hub.api.open_entrance = AsyncMock(return_value={"ok": True, "state": "confirmed"})
    await button.async_press()
    hub.api.open_entrance = AsyncMock(return_value={"ok": False, "error": "sip_unavailable"})
    with pytest.raises(HomeAssistantError, match="riavvialo"):
        await button.async_press()
