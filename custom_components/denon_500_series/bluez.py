"""Lists paired Classic Bluetooth devices from BlueZ over the system D-Bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

_DEVICE_INTERFACE = "org.bluez.Device1"


class BluezError(Exception):
    pass


@dataclass(frozen=True)
class PairedDevice:
    address: str
    name: str

    @property
    def looks_like_receiver(self) -> bool:
        return "denon" in self.name.lower()


async def paired_classic_devices() -> list[PairedDevice]:
    """Paired BR/EDR devices, likely receivers first. Raises BluezError if BlueZ can't be queried."""
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    except (OSError, ValueError) as err:
        raise BluezError(f"cannot connect to the system D-Bus: {err}") from err
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path="/",
                interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects",
            )
        )
    finally:
        bus.disconnect()
    if reply.message_type is MessageType.ERROR:
        raise BluezError(f"BlueZ GetManagedObjects failed: {reply.error_name} {reply.body}")

    return parse_managed_objects(reply.body[0])


def parse_managed_objects(objects: dict[str, dict[str, dict[str, Any]]]) -> list[PairedDevice]:
    devices: list[PairedDevice] = []
    for interfaces in objects.values():
        props = interfaces.get(_DEVICE_INTERFACE)
        # Only BR/EDR devices carry a Class of Device; BLE-only devices can't do RFCOMM.
        if props is None or "Class" not in props or not props["Paired"].value:
            continue
        address: str = props["Address"].value
        name_variant = props.get("Alias") or props.get("Name")
        name: str = address if name_variant is None else name_variant.value
        devices.append(PairedDevice(address=address, name=name))
    return sorted(devices, key=lambda d: (not d.looks_like_receiver, d.name.lower()))
