from __future__ import annotations

import asyncio
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

from .bluez import BluezError, ClassicDevice, pair, paired_classic_devices, scan_classic_devices
from .const import DOMAIN
from .transport import RfcommTransport

_LOGGER = logging.getLogger(__name__)

_MAC = re.compile(r"^[0-9A-F]{2}([:-]?)[0-9A-F]{2}(\1[0-9A-F]{2}){4}$")
DEFAULT_NAME = "Denon AVR"
SCAN_SECONDS = 60.0


def normalize_address(raw: str) -> str:
    value = raw.strip().upper()
    if not _MAC.match(value):
        raise ValueError(f"not a Bluetooth address: {raw!r}")
    digits = re.sub(r"[^0-9A-F]", "", value)
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


class DenonBtConfigFlow(ConfigFlow, domain=DOMAIN):
    """Menu (scan / paired / manual) -> pick -> optional pairing -> connection test -> entry."""

    VERSION = 1

    def __init__(self) -> None:
        self._paired: list[ClassicDevice] = []
        self._found: list[ClassicDevice] = []
        self._scan_task: asyncio.Task[list[ClassicDevice]] | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._address: str | None = None
        self._name = DEFAULT_NAME
        # Form step to return to when pairing or the connection test fails.
        self._origin = "found"
        self._pending_error: str | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        try:
            self._paired = self._unconfigured(await paired_classic_devices())
        except BluezError as err:
            _LOGGER.warning("cannot list paired Bluetooth devices: %s", err)
            self._paired = []
        options = ["scan", "manual"]
        if self._paired:
            options.insert(1, "paired")
        return self.async_show_menu(step_id="user", menu_options=options)

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(scan_classic_devices(SCAN_SECONDS))
        if not self._scan_task.done():
            return self.async_show_progress(step_id="scan", progress_action="scan", progress_task=self._scan_task)
        try:
            devices = self._scan_task.result()
        except BluezError as err:
            _LOGGER.warning("Bluetooth scan failed: %s", err)
            return self.async_show_progress_done(next_step_id="scan_failed")
        finally:
            self._scan_task = None
        _LOGGER.debug("scan found %s", devices)
        self._found = self._unconfigured([d for d in devices if not d.paired])
        if not self._found:
            return self.async_show_progress_done(next_step_id="not_found")
        return self.async_show_progress_done(next_step_id="found")

    async def async_step_not_found(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="not_found", menu_options=["scan", "manual"])

    async def async_step_scan_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="scan_failed", menu_options=["scan", "manual"])

    async def async_step_found(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._pick_step("found", self._found, user_input)

    async def async_step_paired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._pick_step("paired", self._paired, user_input)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._origin = "manual"
        errors = self._take_pending_error()
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            try:
                self._address = normalize_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(self._address)
                self._abort_if_unique_id_configured()
                return await self.async_step_finish()
        schema = probatio.Schema(
            {
                probatio.Required(CONF_ADDRESS, default=self._address or ""): str,
                probatio.Required(CONF_NAME, default=self._name): str,
            }
        )
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

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
            return self.async_show_progress_done(next_step_id=self._origin)
        finally:
            self._pair_task = None
        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        address = self._require_address()
        try:
            transport = await RfcommTransport.open(address)
            await transport.close()
        except (OSError, TimeoutError) as err:
            _LOGGER.warning("cannot reach %s: %s", address, err)
            self._pending_error = "cannot_connect"
            if self._origin == "manual":
                return await self.async_step_manual()
            if self._origin == "paired":
                return await self.async_step_paired()
            return await self.async_step_found()
        return self.async_create_entry(title=self._name, data={CONF_ADDRESS: address})

    async def _pick_step(
        self, step_id: str, devices: list[ClassicDevice], user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        self._origin = step_id
        if user_input is not None:
            self._name = user_input[CONF_NAME]
            self._address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(self._address)
            self._abort_if_unique_id_configured()
            device = next(d for d in devices if d.address == self._address)
            if not device.paired:
                return await self.async_step_pair()
            return await self.async_step_finish()

        preferred = next((d for d in devices if d.looks_like_receiver), devices[0])
        if self._name == DEFAULT_NAME and preferred.looks_like_receiver:
            self._name = preferred.name
        options = [SelectOptionDict(value=d.address, label=f"{d.name} ({d.address})") for d in devices]
        schema = probatio.Schema(
            {
                probatio.Required(CONF_ADDRESS, default=self._address or preferred.address): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                ),
                probatio.Required(CONF_NAME, default=self._name): str,
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=self._take_pending_error())

    def _unconfigured(self, devices: list[ClassicDevice]) -> list[ClassicDevice]:
        configured = {entry.unique_id for entry in self._async_current_entries()}
        return [d for d in devices if d.address not in configured]

    def _take_pending_error(self) -> dict[str, str]:
        if self._pending_error is None:
            return {}
        error, self._pending_error = self._pending_error, None
        return {"base": error}

    def _require_address(self) -> str:
        if self._address is None:
            raise RuntimeError("no receiver address chosen before pairing or connecting")
        return self._address
