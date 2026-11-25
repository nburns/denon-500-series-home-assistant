"""Finds and pairs Classic Bluetooth devices through BlueZ over the system D-Bus."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt16, DBusUInt32
from dbus_fast.errors import DBusError
from dbus_fast.service import ServiceInterface, dbus_method

_LOGGER = logging.getLogger(__name__)

_BLUEZ = "org.bluez"
_ADAPTER_INTERFACE = "org.bluez.Adapter1"
_DEVICE_INTERFACE = "org.bluez.Device1"
PAIR_TIMEOUT = 60.0
# BlueZ drops unpaired devices about 30 s after discovery stops, so pairing may need to find it again.
REDISCOVER_TIMEOUT = 30.0
_AGENT_PATH = "/org/homeassistant/denon_500_series/pairing_agent"
# Fallback for receivers that use legacy PIN pairing; Denon's default.
LEGACY_PIN = "0000"
POLL_INTERVAL = 1.0
# Address prefixes the official app treats as Denon/Marantz receivers.
_RECEIVER_OUIS = frozenset({"00:1F:47", "08:EF:3B", "C4:30:18", "54:15:89"})


class BluezError(Exception):
    pass


class _PairingAgent(ServiceInterface):
    """Answers BlueZ's pairing questions without a user present; registered only for one Pair call."""

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @dbus_method()
    def Release(self) -> None:  # noqa: N802
        pass

    @dbus_method()
    def RequestPinCode(self, device: DBusObjectPath) -> DBusStr:  # noqa: N802
        _LOGGER.info("%s asked for a PIN; sending %s", device, LEGACY_PIN)
        return LEGACY_PIN

    @dbus_method()
    def DisplayPinCode(self, device: DBusObjectPath, pincode: DBusStr) -> None:  # noqa: N802
        _LOGGER.info("%s shows PIN %s", device, pincode)

    @dbus_method()
    def RequestPasskey(self, device: DBusObjectPath) -> DBusUInt32:  # noqa: N802
        raise DBusError("org.bluez.Error.Rejected", "no keypad available to enter a passkey")

    @dbus_method()
    def DisplayPasskey(self, device: DBusObjectPath, passkey: DBusUInt32, entered: DBusUInt16) -> None:  # noqa: N802
        _LOGGER.info("%s shows passkey %06d", device, passkey)

    @dbus_method()
    def RequestConfirmation(self, device: DBusObjectPath, passkey: DBusUInt32) -> None:  # noqa: N802
        _LOGGER.info("confirming passkey %06d for %s", passkey, device)

    @dbus_method()
    def RequestAuthorization(self, device: DBusObjectPath) -> None:  # noqa: N802
        pass

    @dbus_method()
    def AuthorizeService(self, device: DBusObjectPath, uuid: DBusStr) -> None:  # noqa: N802
        # Refusing keeps the receiver from opening audio profiles to this host, which would switch its input.
        raise DBusError("org.bluez.Error.Rejected", f"service {uuid} not wanted")

    @dbus_method()
    def Cancel(self) -> None:  # noqa: N802
        _LOGGER.info("BlueZ cancelled the pairing request")


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
    _LOGGER.debug("D-Bus call %s %s.%s %s", path, interface, member, body or "")
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
        _LOGGER.debug("D-Bus error from %s.%s: %s %s", interface, member, reply.error_name, reply.body)
        raise BluezError(f"{interface}.{member} failed: {reply.error_name} {reply.body}")
    return reply.body


async def _managed_objects(bus: MessageBus) -> dict[str, dict[str, dict[str, Any]]]:
    body = await _call(bus, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    return body[0]


def _adapter_path(objects: dict[str, dict[str, dict[str, Any]]]) -> str:
    adapters = sorted(path for path, interfaces in objects.items() if _ADAPTER_INTERFACE in interfaces)
    if not adapters:
        raise BluezError("no Bluetooth adapter found")
    props = objects[adapters[0]][_ADAPTER_INTERFACE]
    _LOGGER.debug(
        "using adapter %s (of %s): %s",
        adapters[0],
        adapters,
        _describe(props, ("Address", "Name", "Powered", "Pairable")),
    )
    return adapters[0]


def _describe(props: dict[str, Any], keys: tuple[str, ...]) -> str:
    return ", ".join(f"{k}={props[k].value!r}" for k in keys if k in props)


_DEVICE_KEYS = ("Name", "Alias", "Class", "RSSI", "Paired", "Bonded", "Trusted", "Connected", "LegacyPairing")


async def paired_classic_devices() -> list[ClassicDevice]:
    """Paired BR/EDR devices, likely receivers first. Raises BluezError if BlueZ can't be queried."""
    bus = await _connect()
    try:
        objects = await _managed_objects(bus)
    finally:
        bus.disconnect()
    paired = [d for d in parse_managed_objects(objects) if d.paired]
    _LOGGER.debug("paired Classic devices: %s", paired)
    return paired


@asynccontextmanager
async def _discovering(bus: MessageBus, adapter: str) -> AsyncGenerator[None]:
    await _call(bus, adapter, _ADAPTER_INTERFACE, "SetDiscoveryFilter", "a{sv}", [{"Transport": Variant("s", "bredr")}])
    await _call(bus, adapter, _ADAPTER_INTERFACE, "StartDiscovery")
    try:
        yield
    finally:
        await _call(bus, adapter, _ADAPTER_INTERFACE, "StopDiscovery")


async def scan_classic_devices(max_duration: float) -> list[ClassicDevice]:
    """Runs a BR/EDR inquiry until an unpaired receiver shows up or max_duration passes.

    Returns paired and newly found devices, likely receivers first.
    """
    bus = await _connect()
    try:
        adapter = _adapter_path(await _managed_objects(bus))
        _LOGGER.debug("scanning for up to %.0f s", max_duration)
        seen: set[str] = set()
        async with _discovering(bus, adapter):
            loop = asyncio.get_running_loop()
            started = loop.time()
            while True:
                objects = await _managed_objects(bus)
                _log_new_devices(objects, seen, started)
                devices = parse_managed_objects(objects)
                if any(d.looks_like_receiver and not d.paired for d in devices):
                    _LOGGER.debug("receiver found after %.1f s; stopping scan", loop.time() - started)
                    break
                if loop.time() - started >= max_duration:
                    _LOGGER.debug("no unpaired receiver after %.0f s; stopping scan", max_duration)
                    break
                await asyncio.sleep(POLL_INTERVAL)
    finally:
        # Disconnecting also drops this client's discovery session, agent and filter if cleanup never ran.
        bus.disconnect()
    _LOGGER.debug("scan result: %s", devices)
    return devices


def _log_new_devices(objects: dict[str, dict[str, dict[str, Any]]], seen: set[str], started: float) -> None:
    elapsed = asyncio.get_running_loop().time() - started
    for path, interfaces in objects.items():
        props = interfaces.get(_DEVICE_INTERFACE)
        if props is None or path in seen:
            continue
        seen.add(path)
        _LOGGER.debug("%.1f s: %s %s: %s", elapsed, path, props["Address"].value, _describe(props, _DEVICE_KEYS))


def _device_path(objects: dict[str, dict[str, dict[str, Any]]], address: str) -> str | None:
    return next(
        (
            p
            for p, interfaces in objects.items()
            if _DEVICE_INTERFACE in interfaces and interfaces[_DEVICE_INTERFACE]["Address"].value == address
        ),
        None,
    )


async def _find_device(bus: MessageBus, address: str) -> str:
    objects = await _managed_objects(bus)
    path = _device_path(objects, address)
    if path is not None:
        _LOGGER.debug("BlueZ still knows %s: %s", address, _describe(objects[path][_DEVICE_INTERFACE], _DEVICE_KEYS))
        return path
    _LOGGER.debug("BlueZ has forgotten %s since the scan; scanning again for up to %.0f s", address, REDISCOVER_TIMEOUT)
    async with _discovering(bus, _adapter_path(objects)):
        deadline = asyncio.get_running_loop().time() + REDISCOVER_TIMEOUT
        while (path := _device_path(await _managed_objects(bus), address)) is None:
            if asyncio.get_running_loop().time() >= deadline:
                raise BluezError(f"{address} not found; is the receiver still in pairing mode?")
            await asyncio.sleep(POLL_INTERVAL)
    _LOGGER.debug("found %s again at %s", address, path)
    return path


async def pair(address: str) -> None:
    """Pairs with a device, finding it again first if BlueZ has forgotten it since the scan."""
    bus = await _connect()
    try:
        path = await _find_device(bus, address)
        bus.export(_AGENT_PATH, _PairingAgent())
        await _call(
            bus, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os", [_AGENT_PATH, "NoInputNoOutput"]
        )
        _LOGGER.debug("pairing agent registered; calling Pair on %s", path)
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(PAIR_TIMEOUT):
                reply = await bus.call(
                    Message(destination=_BLUEZ, path=path, interface=_DEVICE_INTERFACE, member="Pair")
                )
        except TimeoutError:
            _LOGGER.warning("Pair on %s gave no answer within %.0f s", address, PAIR_TIMEOUT)
            raise
        finally:
            await _call(bus, "/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", "o", [_AGENT_PATH])
        elapsed = asyncio.get_running_loop().time() - started
        after = _describe((await _managed_objects(bus)).get(path, {}).get(_DEVICE_INTERFACE, {}), _DEVICE_KEYS)
        if reply.message_type is MessageType.ERROR and reply.error_name != "org.bluez.Error.AlreadyExists":
            _LOGGER.warning(
                "Pair on %s failed after %.1f s: %s %s; device now: %s",
                address,
                elapsed,
                reply.error_name,
                reply.body,
                after,
            )
            raise BluezError(f"{reply.error_name}: {' '.join(str(b) for b in reply.body)}")
        _LOGGER.info("paired with %s in %.1f s (%s); device now: %s", address, elapsed, reply.error_name or "ok", after)
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
