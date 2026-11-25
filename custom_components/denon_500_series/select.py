from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DenonBtConfigEntry
from .connection import ReceiverConnection, ReceiverState
from .entity import DenonBtEntity
from .protocol import TUNER_SOURCES

PARALLEL_UPDATES = 1
_AUTO = "auto"
_MANUAL = "manual"


async def async_setup_entry(
    hass: HomeAssistant, entry: DenonBtConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    connection = entry.runtime_data
    async_add_entities([TunerPresetSelect(entry, connection), TuningModeSelect(entry, connection)])


class TunerPresetSelect(DenonBtEntity, SelectEntity):
    """Recalls a tuner preset; offered only while FM or AM is the input."""

    _attr_translation_key = "tuner_preset"

    def __init__(self, entry: DenonBtConfigEntry, connection: ReceiverConnection) -> None:
        self._numbers: dict[str, int] = {}
        super().__init__(entry, connection, key="tuner_preset")

    def _update_attrs(self, state: ReceiverState) -> None:
        super()._update_attrs(state)
        self._numbers = {f"{p.number}: {p.label}": p.number for p in state.presets or ()}
        self._attr_options = list(self._numbers)
        self._attr_available = state.connected and state.source in TUNER_SOURCES and bool(self._numbers)
        self._attr_current_option = next(
            (option for option, number in self._numbers.items() if number == state.tuner_preset), None
        )

    async def async_select_option(self, option: str) -> None:
        if option not in self._numbers:
            raise ServiceValidationError(f"unknown preset {option!r}")
        await self._run(self._connection.recall_preset(self._numbers[option]))


class TuningModeSelect(DenonBtEntity, SelectEntity):
    _attr_translation_key = "tuning_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: DenonBtConfigEntry, connection: ReceiverConnection) -> None:
        super().__init__(entry, connection, key="tuning_mode")
        self._attr_options = [_AUTO, _MANUAL]

    def _update_attrs(self, state: ReceiverState) -> None:
        super()._update_attrs(state)
        self._attr_available = state.connected and state.source in TUNER_SOURCES
        self._attr_current_option = None if state.tuning_auto is None else _AUTO if state.tuning_auto else _MANUAL

    async def async_select_option(self, option: str) -> None:
        if option not in self._attr_options:
            raise ServiceValidationError(f"unknown tuning mode {option!r}")
        await self._run(self._connection.set_tuning_mode(option == _AUTO))
