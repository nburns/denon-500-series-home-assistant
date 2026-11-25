from __future__ import annotations

import probatio
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback, async_get_current_platform

from . import DenonBtConfigEntry
from .connection import ReceiverState
from .const import SOURCE_LABELS
from .entity import DenonBtEntity
from .protocol import PRESET_COUNT, TUNER_SOURCES, USB_SOURCES, Source

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
_BASE_FEATURES = (
    MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.SELECT_SOURCE
)
_SKIP_FEATURES = MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK
_PLAY_FEATURES = MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE
SERVICE_STORE_PRESET = "store_preset"
ATTR_PRESET = "preset"


async def async_setup_entry(
    hass: HomeAssistant, entry: DenonBtConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DenonBtMediaPlayer(entry, entry.runtime_data)])
    async_get_current_platform().async_register_entity_service(  # pyright: ignore[reportUnknownMemberType]
        SERVICE_STORE_PRESET,
        {probatio.Required(ATTR_PRESET): probatio.All(probatio.Coerce(int), probatio.Range(min=1, max=PRESET_COUNT))},
        "async_store_preset",
    )


class DenonBtMediaPlayer(DenonBtEntity, MediaPlayerEntity):
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def _update_attrs(self, state: ReceiverState) -> None:
        super()._update_attrs(state)
        self._attr_state = None if state.power is None else MediaPlayerState.ON if state.power else MediaPlayerState.OFF
        volume = state.volume
        self._attr_volume_level = None if volume is None or volume.max_raw == 0 else volume.raw / volume.max_raw
        self._attr_is_volume_muted = state.muted
        self._attr_source = None if state.source is None else SOURCE_LABELS.get(state.source, state.source.name)
        sources = state.supported_sources or _DEFAULT_SOURCES
        self._attr_source_list = [SOURCE_LABELS.get(s, s.name) for s in sources]
        self._attr_sound_mode = state.sound_mode
        features = _BASE_FEATURES
        if state.source in TUNER_SOURCES:
            features |= _SKIP_FEATURES
        elif state.source in USB_SOURCES:
            features |= _SKIP_FEATURES | _PLAY_FEATURES
        self._attr_supported_features = features
        self._attr_media_title = state.tuner_station
        self._attr_media_channel = None if state.tuner_preset is None else f"Preset {state.tuner_preset}"

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

    async def async_media_next_track(self) -> None:
        await self._run(self._connection.next_track())

    async def async_media_previous_track(self) -> None:
        await self._run(self._connection.previous_track())

    async def async_media_play(self) -> None:
        await self._run(self._connection.play())

    async def async_media_pause(self) -> None:
        await self._run(self._connection.pause())

    async def async_store_preset(self, preset: int) -> None:
        await self._run(self._connection.store_preset(preset))
