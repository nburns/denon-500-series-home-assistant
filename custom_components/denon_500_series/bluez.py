"""Finds and pairs Classic Bluetooth devices through BlueZ over the system D-Bus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

_BLUEZ = "org.bluez"
_ADAPTER_INTERFACE = "org.bluez.Adapter1"
_DEVICE_INTERFACE = "org.bluez.Device1"
PAIR_TIMEOUT = 60.0
POLL_INTERVAL = 1.0
# Address prefixes the official app treats as Denon/Marantz receivers.
_RECEIVER_OUIS = frozenset({"00:1F:47", "08:EF:3B", "C4:30:18", "54:15:89"})


class BluezError(Exception):
    pass


@dataclass(frozen=True)
class ClassicDevice:
    address: str
    name: str
    paired: bool

    @property
    def looks_like_receiver(self) -> bool:
        return self.address[:8].upper() in _RECEIVER_OUIS or "denon" in self.name.lower()


async def _connect() -> MessageBus:
    try:
        return await MessageBus(bus_type=BusType.SYSTEM).connect()
    except (OSError, ValueError) as err:
        raise BluezError(f"cannot connect to the system D-Bus: {err}") from err


async def _call(
    bus: MessageBus, path: str, interface: str, member: str, signature: str = "", body: list[Any] | None = None
) -> list[Any]:
    reply = await bus.call(
        Message(
            destination=_BLUEZ,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )
    )
    if reply.message_type is MessageType.ERROR:
        raise BluezError(f"{interface}.{member} failed: {reply.error_name} {reply.body}")
    return reply.body


async def _managed_objects(bus: MessageBus) -> dict[str, dict[str, dict[str, Any]]]:
    body = await _call(bus, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    return body[0]


def _adapter_path(objects: dict[str, dict[str, dict[str, Any]]]) -> str:
    adapters = sorted(path for path, interfaces in objects.items() if _ADAPTER_INTERFACE in interfaces)
    if not adapters:
        raise BluezError("no Bluetooth adapter found")
    return adapters[0]


async def paired_classic_devices() -> list[ClassicDevice]:
    """Paired BR/EDR devices, likely receivers first. Raises BluezError if BlueZ can't be queried."""
    bus = await _connect()
    try:
        objects = await _managed_objects(bus)
    finally:
        bus.disconnect()
    return [d for d in parse_managed_objects(objects) if d.paired]


async def scan_classic_devices(max_duration: float) -> list[ClassicDevice]:
    """Runs a BR/EDR inquiry until an unpaired receiver shows up or max_duration passes.

    Returns paired and newly found devices, likely receivers first.
    """
    bus = await _connect()
    try:
        adapter = _adapter_path(await _managed_objects(bus))
        await _call(
            bus, adapter, _ADAPTER_INTERFACE, "SetDiscoveryFilter", "a{sv}", [{"Transport": Variant("s", "bredr")}]
        )
        await _call(bus, adapter, _ADAPTER_INTERFACE, "StartDiscovery")
        try:
            deadline = asyncio.get_running_loop().time() + max_duration
            while True:
                devices = parse_managed_objects(await _managed_objects(bus))
                if any(d.looks_like_receiver and not d.paired for d in devices):
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(POLL_INTERVAL)
        finally:
            await _call(bus, adapter, _ADAPTER_INTERFACE, "StopDiscovery")
    finally:
        # Disconnecting also drops this client's discovery session and filter if StopDiscovery never ran.
        bus.disconnect()
    return devices


async def pair(address: str) -> None:
    """Pairs with a device found by a previous scan. No agent is registered, so BlueZ uses Just Works pairing."""
    bus = await _connect()
    try:
        objects = await _managed_objects(bus)
        path = next(
            (
                p
                for p, interfaces in objects.items()
                if _DEVICE_INTERFACE in interfaces and interfaces[_DEVICE_INTERFACE]["Address"].value == address
            ),
            None,
        )
        if path is None:
            raise BluezError(f"{address} is not known to BlueZ; scan again with the receiver in pairing mode")
        async with asyncio.timeout(PAIR_TIMEOUT):
            reply = await bus.call(Message(destination=_BLUEZ, path=path, interface=_DEVICE_INTERFACE, member="Pair"))
        if reply.message_type is MessageType.ERROR and reply.error_name != "org.bluez.Error.AlreadyExists":
            raise BluezError(f"pairing with {address} failed: {reply.error_name} {reply.body}")
    finally:
        bus.disconnect()


def parse_managed_objects(objects: dict[str, dict[str, dict[str, Any]]]) -> list[ClassicDevice]:
    devices: list[ClassicDevice] = []
    for interfaces in objects.values():
        props = interfaces.get(_DEVICE_INTERFACE)
        # Only BR/EDR devices carry a Class of Device; BLE-only devices can't do RFCOMM.
        if props is None or "Class" not in props:
            continue
        address: str = props["Address"].value
        name_variant = props.get("Alias") or props.get("Name")
        name: str = address if name_variant is None else name_variant.value
        devices.append(ClassicDevice(address=address, name=name, paired=bool(props["Paired"].value)))
    return sorted(devices, key=lambda d: (not d.paired, not d.looks_like_receiver, d.name.lower()))
