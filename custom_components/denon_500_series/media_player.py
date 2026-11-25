from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DenonBtConfigEntry
from .connection import ReceiverConnection, ReceiverState
from .const import DOMAIN, SOURCE_LABELS
from .protocol import ProtocolError, Source

# What the AVR-S510BT reports; shown only until the receiver answers the supported-sources query.
_DEFAULT_SOURCES = (
    Source.CBL_SAT,
    Source.DVD_BLURAY,
    Source.BLURAY,
    Source.GAME,
    Source.MEDIA_PLAYER,
    Source.TV_AUDIO,
    Source.USB1,
    Source.FM,
    Source.AM,
    Source.BLUETOOTH,
)
_SOURCES_BY_LABEL = {label: source for source, label in SOURCE_LABELS.items()}
# Reported maximum on the AVR-S510BT; used only until the receiver reports its own.
_FALLBACK_MAX_RAW = 0xC5
# One serial link; commands are serialized by the connection anyway.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: DenonBtConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DenonBtMediaPlayer(entry, entry.runtime_data)])


class DenonBtMediaPlayer(MediaPlayerEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, entry: DenonBtConfigEntry, connection: ReceiverConnection) -> None:
        self._connection = connection
        address: str = entry.data[CONF_ADDRESS]
        self._attr_unique_id = address
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
        self._attr_state = None if state.power is None else MediaPlayerState.ON if state.power else MediaPlayerState.OFF
        volume = state.volume
        self._attr_volume_level = None if volume is None or volume.max_raw == 0 else volume.raw / volume.max_raw
        self._attr_is_volume_muted = state.muted
        self._attr_source = None if state.source is None else SOURCE_LABELS.get(state.source, state.source.name)
        sources = state.supported_sources or _DEFAULT_SOURCES
        self._attr_source_list = [SOURCE_LABELS.get(s, s.name) for s in sources]
        self._attr_sound_mode = state.sound_mode

    async def _run(self, command: Awaitable[object]) -> None:
        try:
            await command
        except (ConnectionError, TimeoutError, ProtocolError, RuntimeError) as err:
            raise HomeAssistantError(str(err)) from err

    async def async_turn_on(self) -> None:
        await self._run(self._connection.set_power(True))

    async def async_turn_off(self) -> None:
        await self._run(self._connection.set_power(False))

    async def async_volume_up(self) -> None:
        await self._run(self._connection.volume_step(up=True))

    async def async_volume_down(self) -> None:
        await self._run(self._connection.volume_step(up=False))

    async def async_set_volume_level(self, volume: float) -> None:
        current = self._connection.state.volume
        max_raw = current.max_raw if current is not None else _FALLBACK_MAX_RAW
        await self._run(self._connection.set_volume_raw(round(volume * max_raw)))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._run(self._connection.set_mute(mute))

    async def async_select_source(self, source: str) -> None:
        if source not in _SOURCES_BY_LABEL:
            raise ServiceValidationError(f"unknown source {source!r}")
        await self._run(self._connection.select_source(_SOURCES_BY_LABEL[source]))
