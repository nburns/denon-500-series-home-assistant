from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
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

from .bluez import BluezError, ClassicDevice, pair, paired_classic_devices, scan_classic_devices
from .const import DOMAIN
from .transport import RfcommTransport

_LOGGER = logging.getLogger(__name__)

_MAC = re.compile(r"^[0-9A-F]{2}([:-]?)[0-9A-F]{2}(\1[0-9A-F]{2}){4}$")
DEFAULT_NAME = "Denon AVR"
SCAN_SECONDS = 60.0
SCAN_OPTION = "scan"


def normalize_address(raw: str) -> str:
    value = raw.strip().upper()
    if not _MAC.match(value):
        raise ValueError(f"not a Bluetooth address: {raw!r}")
    digits = re.sub(r"[^0-9A-F]", "", value)
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


class DenonBtConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._devices: list[ClassicDevice] | None = None
        self._scanned = False
        self._scan_task: asyncio.Task[list[ClassicDevice]] | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._address: str | None = None
        self._name = DEFAULT_NAME
        self._pending_error: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._devices is None:
            try:
                self._devices = await paired_classic_devices()
            except BluezError as err:
                # Still usable: the form falls back to typing the address.
                _LOGGER.warning("cannot list paired Bluetooth devices: %s", err)
                self._devices = []
            if not self._devices:
                return await self.async_step_scan()

        if user_input is None:
            return self._show_user_form()

        self._name = user_input[CONF_NAME]
        if user_input[CONF_ADDRESS] == SCAN_OPTION:
            return await self.async_step_scan()
        try:
            self._address = normalize_address(user_input[CONF_ADDRESS])
        except ValueError:
            return self._show_user_form({CONF_ADDRESS: "invalid_address"})
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()

        device = next((d for d in self._devices if d.address == self._address), None)
        if device is not None and not device.paired:
            return await self.async_step_pair()
        return await self.async_step_finish()

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(scan_classic_devices(SCAN_SECONDS))
        if not self._scan_task.done():
            return self.async_show_progress(step_id="scan", progress_action="scan", progress_task=self._scan_task)
        try:
            found = self._scan_task.result()
        except BluezError as err:
            _LOGGER.warning("Bluetooth scan failed: %s", err)
            self._pending_error = "scan_failed"
        else:
            self._devices = found
            self._scanned = True
            if not any(d.looks_like_receiver and not d.paired for d in found):
                self._pending_error = "no_devices_found"
        finally:
            self._scan_task = None
        return self.async_show_progress_done(next_step_id="user")

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        address = self._require_address()
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(pair(address))
        if not self._pair_task.done():
            return self.async_show_progress(step_id="pair", progress_action="pair", progress_task=self._pair_task)
        try:
            self._pair_task.result()
        except (BluezError, TimeoutError) as err:
            _LOGGER.warning("pairing with %s failed: %s", address, err)
            self._pending_error = "pairing_failed"
            return self.async_show_progress_done(next_step_id="user")
        finally:
            self._pair_task = None
        self._devices = [replace(d, paired=True) if d.address == address else d for d in self._devices or []]
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        address = self._require_address()
        try:
            transport = await RfcommTransport.open(address)
            await transport.close()
        except (OSError, TimeoutError) as err:
            _LOGGER.warning("cannot reach %s: %s", address, err)
            return self._show_user_form({"base": "cannot_connect"})
        return self.async_create_entry(title=self._name, data={CONF_ADDRESS: address})

    def _require_address(self) -> str:
        if self._address is None:
            raise RuntimeError("no receiver address chosen before pairing or connecting")
        return self._address

    def _show_user_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        errors = dict(errors or {})
        if self._pending_error is not None:
            errors.setdefault("base", self._pending_error)
            self._pending_error = None
        devices = self._devices or []
        configured = {entry.unique_id for entry in self._async_current_entries()}
        options = [
            SelectOptionDict(
                value=d.address, label=f"{d.name} ({d.address})" if d.paired else f"{d.name} ({d.address}) - pair now"
            )
            for d in devices
            if d.address not in configured
        ]
        options.append(SelectOptionDict(value=SCAN_OPTION, label="Scan again" if self._scanned else "Scan for devices"))
        if self._name == DEFAULT_NAME:
            self._name = next((d.name for d in devices if d.looks_like_receiver), DEFAULT_NAME)
        # custom_value lets the dropdown also accept a typed address for devices BlueZ didn't list.
        schema = probatio.Schema(
            {
                probatio.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
                ),
                probatio.Required(CONF_NAME, default=self._name): str,
            }
        )
        receiver = next((d.address for d in devices if d.looks_like_receiver and d.address not in configured), None)
        suggested = {CONF_ADDRESS: self._address or receiver}
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(schema, suggested), errors=errors
        )
