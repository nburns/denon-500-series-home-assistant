from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DenonBtConfigEntry
from .connection import ReceiverConnection
from .entity import DenonBtEntity
from .protocol import QUICK_SELECT_SLOTS

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: DenonBtConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    connection = entry.runtime_data
    async_add_entities(
        [
            *(QuickSelectButton(entry, connection, slot) for slot in range(1, QUICK_SELECT_SLOTS + 1)),
            SoundModeButton(entry, connection),
        ]
    )


class QuickSelectButton(DenonBtEntity, ButtonEntity):
    _attr_translation_key = "quick_select"

    def __init__(self, entry: DenonBtConfigEntry, connection: ReceiverConnection, slot: int) -> None:
        super().__init__(entry, connection, key=f"quick_select_{slot}")
        self._slot = slot
        self._attr_translation_placeholders = {"slot": str(slot)}

    async def async_press(self) -> None:
        await self._run(self._connection.quick_select(self._slot))


class SoundModeButton(DenonBtEntity, ButtonEntity):
    """The app's single Sound Mode button; the receiver picks the next mode."""

    _attr_translation_key = "sound_mode"

    def __init__(self, entry: DenonBtConfigEntry, connection: ReceiverConnection) -> None:
        super().__init__(entry, connection, key="sound_mode")

    async def async_press(self) -> None:
        await self._run(self._connection.press_sound_mode())
