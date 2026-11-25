"""Persistent connection to the receiver. No Home Assistant imports, so the dev CLI can reuse it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Protocol, TypeVar

from . import protocol
from .protocol import Event, Frame, Source

_LOGGER = logging.getLogger(__name__)

REPLY_TIMEOUT = 3.0
MAX_VOLUME_STEPS = 200

E = TypeVar("E", bound=Event)


class Transport(Protocol):
    async def read(self) -> bytes:
        """Return the next chunk of received bytes. Raise ConnectionError when the link is gone."""
        ...

    async def write(self, data: bytes) -> None: ...

    async def close(self) -> None: ...


TransportFactory = Callable[[], Awaitable[Transport]]


class NotConnectedError(ConnectionError):
    pass


@dataclass(frozen=True)
class ReceiverState:
    connected: bool = False
    power: bool | None = None
    bluetooth_standby: bool | None = None
    source: Source | None = None
    supported_sources: tuple[Source, ...] | None = None
    volume: protocol.Volume | None = None
    muted: bool | None = None
    sound_mode: str | None = None


@dataclass
class _Waiter:
    kind: type[Event]
    future: asyncio.Future[Event] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class ReceiverConnection:
    def __init__(
        self,
        transport_factory: TransportFactory,
        *,
        reconnect_min: float = 5.0,
        reconnect_max: float = 300.0,
    ) -> None:
        self._transport_factory = transport_factory
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._transport: Transport | None = None
        self._decoder = protocol.FrameDecoder()
        self._waiters: list[_Waiter] = []
        self._listeners: list[Callable[[ReceiverState], None]] = []
        self._command_lock = asyncio.Lock()
        # Latest requested raw volume; a seek in progress follows it so slider drags don't queue stale targets.
        self._volume_target: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self.state = ReceiverState()

    def add_listener(self, listener: Callable[[ReceiverState], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("connection already started")
        self._task = asyncio.create_task(self._run(), name="denon_500_series connection")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def wait_connected(self, timeout: float) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def _run(self) -> None:
        delay = self._reconnect_min
        while True:
            try:
                transport = await self._transport_factory()
            except (OSError, TimeoutError) as err:
                _LOGGER.warning("connect failed: %s; retrying in %.0fs", err, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max)
                continue
            delay = self._reconnect_min
            try:
                await self._session(transport)
            except (OSError, protocol.ProtocolError) as err:
                _LOGGER.warning("connection lost: %s", err)
            finally:
                self._transport = None
                self._connected.clear()
                self._decoder = protocol.FrameDecoder()
                self._fail_waiters(NotConnectedError("connection lost"))
                self._set_state(replace(ReceiverState(), supported_sources=self.state.supported_sources))
                with contextlib.suppress(OSError):
                    await transport.close()
            await asyncio.sleep(self._reconnect_min)

    async def _session(self, transport: Transport) -> None:
        self._transport = transport
        self._decoder = protocol.FrameDecoder()
        self._connected.set()
        self._set_state(replace(self.state, connected=True))
        _LOGGER.info("connected")
        for frame in (
            protocol.query_power(),
            protocol.query_bluetooth_standby(),
            protocol.query_current_source(),
            protocol.query_supported_sources(),
        ):
            await transport.write(frame.encode())
        while True:
            data = await transport.read()
            for frame in self._decoder.feed(data):
                _LOGGER.debug("recv %s", frame)
                try:
                    event = protocol.decode(frame)
                except protocol.ProtocolError as err:
                    _LOGGER.warning("undecodable frame %s: %s", frame, err)
                    continue
                self._apply(event)

    def _apply(self, event: Event) -> None:
        s = self.state
        match event:
            case protocol.PowerState(on=on):
                s = replace(s, power=on)
            case protocol.BluetoothStandbyState(enabled=enabled):
                s = replace(s, bluetooth_standby=enabled)
            case protocol.CurrentSource(source=source):
                s = replace(s, source=source)
            case protocol.SupportedSources(sources=sources):
                s = replace(s, supported_sources=sources)
            case protocol.VolumeState(source=source, volume=volume):
                s = replace(s, source=source, volume=volume)
            case protocol.MuteState(source=source, muted=muted):
                s = replace(s, source=source, muted=muted)
            case protocol.SoundModeName(source=source, name=name):
                s = replace(s, source=source, sound_mode=name)
            case protocol.FeatureState(source=source, muted=muted, volume=volume, sound_mode=mode):
                s = replace(s, source=source, muted=muted, volume=volume, sound_mode=mode)
            case protocol.Unrecognized():
                pass
        self._set_state(s)
        for waiter in list(self._waiters):
            if isinstance(event, waiter.kind) and not waiter.future.done():
                waiter.future.set_result(event)
                self._waiters.remove(waiter)
                break

    def _set_state(self, state: ReceiverState) -> None:
        if state == self.state:
            return
        self.state = state
        for listener in list(self._listeners):
            try:
                listener(state)
            except Exception:
                _LOGGER.exception("state listener failed")

    def _fail_waiters(self, err: Exception) -> None:
        for waiter in self._waiters:
            if not waiter.future.done():
                waiter.future.set_exception(err)
        self._waiters.clear()

    async def _send(self, frame: Frame) -> None:
        if self._transport is None:
            raise NotConnectedError("receiver is not connected")
        _LOGGER.debug("send %s", frame)
        await self._transport.write(frame.encode())

    async def _request(self, frame: Frame, kind: type[E]) -> E:
        waiter = _Waiter(kind)
        self._waiters.append(waiter)
        try:
            await self._send(frame)
            event = await asyncio.wait_for(waiter.future, REPLY_TIMEOUT)
        except BaseException:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            raise
        if not isinstance(event, kind):
            raise protocol.ProtocolError(f"expected {kind.__name__}, got {event}")
        return event

    def _active_source(self) -> Source:
        if self.state.source is None:
            raise NotConnectedError("active source not known yet")
        return self.state.source

    async def set_power(self, on: bool) -> None:
        async with self._command_lock:
            if not on and self.state.bluetooth_standby is not True:
                raise RuntimeError(
                    "Bluetooth standby is not confirmed on; powering off would drop the link and the "
                    "receiver could not be turned back on over Bluetooth"
                )
            await self._request(protocol.power(on), protocol.PowerState)

    async def select_source(self, source: Source) -> None:
        async with self._command_lock:
            await self._send(protocol.select_source(source))

    async def quick_select(self, slot: int) -> None:
        async with self._command_lock:
            await self._send(protocol.quick_select(slot))

    async def volume_step(self, up: bool) -> protocol.Volume:
        async with self._command_lock:
            return await self._volume_step(up)

    async def _volume_step(self, up: bool) -> protocol.Volume:
        active = self._active_source()
        frame = protocol.volume_up(active) if up else protocol.volume_down(active)
        return (await self._request(frame, protocol.VolumeState)).volume

    async def set_volume_raw(self, target: int) -> protocol.Volume:
        """Step toward an absolute raw level; the protocol has no direct volume set.

        Stops on the target or as soon as a step passes it, since one press may move more than one raw unit.
        """
        self._volume_target = target
        async with self._command_lock:
            volume = self.state.volume
            if volume is None:
                # Volume is only reported in reply to a step, so take one to learn it.
                volume = await self._volume_step(up=False)
            for _ in range(MAX_VOLUME_STEPS):
                wanted = max(0, min(self._volume_target, volume.max_raw))
                if volume.raw == wanted:
                    _LOGGER.debug("volume at raw %d (%s)", volume.raw, volume.display)
                    return volume
                up = wanted > volume.raw
                previous = volume.raw
                volume = await self._volume_step(up=up)
                _LOGGER.debug("volume %s: raw %d -> %d, target %d", "+" if up else "-", previous, volume.raw, wanted)
                if volume.raw == previous:
                    raise protocol.ProtocolError(f"volume stuck at raw {previous} while seeking {wanted}")
                if (volume.raw > wanted) == up and volume.raw != wanted and self._volume_target == wanted:
                    _LOGGER.debug("step passed raw %d; stopping at %d (%s)", wanted, volume.raw, volume.display)
                    return volume
            raise protocol.ProtocolError(f"volume did not reach raw {self._volume_target} in {MAX_VOLUME_STEPS} steps")

    async def set_mute(self, on: bool) -> None:
        async with self._command_lock:
            await self._send(protocol.mute(self._active_source(), on))
            # The receiver does not acknowledge mute changes, so record the requested state.
            self._set_state(replace(self.state, muted=on))

    async def press_sound_mode(self) -> str:
        async with self._command_lock:
            return (await self._request(protocol.sound_mode(self._active_source()), protocol.SoundModeName)).name

    async def set_bluetooth_standby(self, enabled: bool) -> None:
        async with self._command_lock:
            await self._request(protocol.bluetooth_standby(enabled), protocol.BluetoothStandbyState)
