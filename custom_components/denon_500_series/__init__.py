"""Denon AVR control over the receiver's Bluetooth serial (SPP) channel."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .connection import ReceiverConnection, Transport
from .transport import RfcommTransport

PLATFORMS = [Platform.BUTTON, Platform.MEDIA_PLAYER]

type DenonBtConfigEntry = ConfigEntry[ReceiverConnection]


async def async_setup_entry(hass: HomeAssistant, entry: DenonBtConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS]

    async def open_transport() -> Transport:
        return await RfcommTransport.open(address)

    connection = ReceiverConnection(open_transport)
    connection.start()
    entry.runtime_data = connection

    async def _stop(_event: Event) -> None:
        await connection.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DenonBtConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded
