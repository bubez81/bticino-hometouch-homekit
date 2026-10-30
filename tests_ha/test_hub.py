from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.bticino_hometouch.const import BUS_EVENT, DOMAIN
from custom_components.bticino_hometouch.hub import (
    CONNECTED, DISCONNECTED, DOWN_AFTER, STABLE_AFTER, UNSTABLE, UNSTABLE_DROPS, HometouchHub)


async def test_repeated_drops_raise_a_repair_until_the_connection_holds(hass):
    hub = HometouchHub(hass, MagicMock(), {}, "entry1")
    events = async_capture_events(hass, BUS_EVENT)
    registry = ir.async_get(hass)

    # A few drops a day are normal: no warning.
    for _ in range(UNSTABLE_DROPS - 1):
        hub.handle_event({"type": "sip_disconnected", "reason": "ConnectionError"})
    assert registry.async_get_issue(DOMAIN, "gateway_unstable_entry1") is None

    hub.handle_event({"type": "sip_disconnected", "reason": "ConnectionError"})
    issue = registry.async_get_issue(DOMAIN, "gateway_unstable_entry1")
    assert issue is not None and issue.translation_key == "gateway_unstable"
    assert issue.translation_placeholders["drops"] == str(UNSTABLE_DROPS)
    # More drops do not repeat the warning.
    hub.handle_event({"type": "sip_disconnected", "reason": "ConnectionError"})
    assert [e.data["type"] for e in events].count("gateway_unstable") == 1

    # The connection holds: the warning goes away by itself.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=STABLE_AFTER + 1))
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "gateway_unstable_entry1") is None
    assert events[-1].data["type"] == "gateway_stable"
    assert hub.gateway_unstable is False
    await hub.async_stop()


async def test_connection_status_ignores_normal_reconnections(hass):
    hub = HometouchHub(hass, MagicMock(), {}, "entry1")
    updates = []
    hub.add_listener(updates.append)
    hub.handle_event({"type": "state", "sip_registered": True})
    assert hub.connection == CONNECTED

    # The usual drop, registered again within seconds: still connected.
    hub.handle_event({"type": "sip_disconnected", "reason": "ConnectionError"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DOWN_AFTER - 50))
    hub.handle_event({"type": "sip_registered"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DOWN_AFTER + 1))
    await hass.async_block_till_done()
    assert hub.connection == CONNECTED

    # Not registered again for a minute: disconnected, and the entities are told.
    hub.handle_event({"type": "sip_disconnected", "reason": "ConnectionError"})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DOWN_AFTER + 1))
    await hass.async_block_till_done()
    assert hub.connection == DISCONNECTED
    assert updates[-1] == {"type": "connection_changed"}
    hub.handle_event({"type": "sip_registered"})
    assert hub.connection == CONNECTED
    await hub.async_stop()


async def test_connection_status_unstable_while_the_warning_is_on(hass):
    hub = HometouchHub(hass, MagicMock(), {}, "entry1")
    hub.handle_event({"type": "state", "sip_registered": True})
    for _ in range(UNSTABLE_DROPS):
        hub.handle_event({"type": "sip_disconnected", "reason": "ConnectionError"})
        hub.handle_event({"type": "sip_registered"})
    assert hub.connection == UNSTABLE
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=STABLE_AFTER + 1))
    await hass.async_block_till_done()
    assert hub.connection == CONNECTED
    await hub.async_stop()
