from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from . import DenonBtConfigEntry
from .connection import ReceiverConnection, ReceiverState
from .const import DOMAIN
from .protocol import ProtocolError


class DenonBtEntity(Entity):
    """Shared device info, availability and push updates for every entity of one receiver."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: DenonBtConfigEntry, connection: ReceiverConnection, key: str | None = None) -> None:
        self._connection = connection
        address: str = entry.data[CONF_ADDRESS]
        self._attr_unique_id = address if key is None else f"{address}-{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="Denon",
            name=entry.title,
        )
        self._update_attrs(connection.state)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._connection.add_listener(self._handle_state))
        self._update_attrs(self._connection.state)

    @callback
    def async_entity_id_changed(self, old_entity_id: str) -> None:
        # Nothing here is keyed on entity_id; overriding opts into in-place renames.
        pass

    @callback
    def _handle_state(self, state: ReceiverState) -> None:
        self._update_attrs(state)
        self.async_write_ha_state()

    def _update_attrs(self, state: ReceiverState) -> None:
        self._attr_available = state.connected

    async def _run(self, command: Awaitable[object]) -> None:
        try:
            await command
        except (ConnectionError, TimeoutError, ProtocolError, RuntimeError) as err:
            raise HomeAssistantError(str(err)) from err
