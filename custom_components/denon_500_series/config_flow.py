from __future__ import annotations

import logging
import re
from typing import Any

import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,  # pyright: ignore[reportUnknownVariableType]
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .bluez import BluezError, PairedDevice, paired_classic_devices
from .const import DOMAIN
from .transport import RfcommTransport

_LOGGER = logging.getLogger(__name__)

_MAC = re.compile(r"^[0-9A-F]{2}([:-]?)[0-9A-F]{2}(\1[0-9A-F]{2}){4}$")
DEFAULT_NAME = "Denon AVR"


def normalize_address(raw: str) -> str:
    value = raw.strip().upper()
    if not _MAC.match(value):
        raise ValueError(f"not a Bluetooth address: {raw!r}")
    digits = re.sub(r"[^0-9A-F]", "", value)
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


class DenonBtConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._paired: list[PairedDevice] | None = None

    async def _paired_devices(self) -> list[PairedDevice]:
        if self._paired is None:
            try:
                self._paired = await paired_classic_devices()
            except BluezError as err:
                # Still usable: the form falls back to typing the address.
                _LOGGER.warning("cannot list paired Bluetooth devices: %s", err)
                self._paired = []
        return self._paired

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        paired = await self._paired_devices()
        if user_input is not None:
            try:
                address = normalize_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                try:
                    transport = await RfcommTransport.open(address)
                    await transport.close()
                except (OSError, TimeoutError) as err:
                    _LOGGER.warning("cannot reach %s: %s", address, err)
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title=user_input[CONF_NAME], data={CONF_ADDRESS: address})

        configured = {entry.unique_id for entry in self._async_current_entries()}
        options = [
            SelectOptionDict(value=d.address, label=f"{d.name} ({d.address})")
            for d in paired
            if d.address not in configured
        ]
        default_name = next((d.name for d in paired if d.looks_like_receiver), DEFAULT_NAME)
        # custom_value lets the dropdown also accept a typed address for devices BlueZ didn't list.
        address_field: Any = (  # pyright: ignore[reportUnknownVariableType]
            SelectSelector(SelectSelectorConfig(options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN))
            if options
            else str
        )
        schema = probatio.Schema(
            {
                probatio.Required(CONF_ADDRESS): address_field,
                probatio.Required(CONF_NAME, default=default_name): str,
            }
        )
        suggested: dict[str, Any] | None = user_input
        if suggested is None and options:
            suggested = {CONF_ADDRESS: options[0]["value"]}
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(schema, suggested), errors=errors
        )
