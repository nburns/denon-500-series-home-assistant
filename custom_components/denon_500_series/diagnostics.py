from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import DenonBtConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: DenonBtConfigEntry) -> dict[str, Any]:
    # entry.data holds only the receiver's Bluetooth address, which is left out.
    return {"state": asdict(entry.runtime_data.state)}
