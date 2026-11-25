# pyright: basic
"""macOS RFCOMM transport for local development, via IOBluetooth.

macOS binds /dev/cu.DenonAVR-S510BT to SPP channel 1, which never answers, so the
control channel (2) has to be opened directly. IOBluetooth delivers callbacks on the
main thread's run loop, so everything here runs on the asyncio (main) thread and a
task pumps the run loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import objc
from Foundation import NSDate, NSObject, NSRunLoop  # pyright: ignore[reportAttributeAccessIssue]
from IOBluetooth import IOBluetoothDevice  # pyright: ignore[reportAttributeAccessIssue]

log = logging.getLogger(__name__)

CONTROL_CHANNEL = 2
_IO_SUCCESS = 0
_OPEN_ATTEMPTS = 4
_OPEN_RETRY_DELAY = 1.0
_PUMP_INTERVAL = 0.02


class _ChannelDelegate(NSObject):
    def initWithSink_(self, sink: Any) -> Any:  # noqa: N802
        self = objc.super(_ChannelDelegate, self).init()  # pyright: ignore[reportAttributeAccessIssue]
        self.sink = sink
        return self

    def rfcommChannelData_data_length_(self, _channel: Any, data: Any, length: int) -> None:  # noqa: N802
        self.sink(bytes(data[:length]))

    def rfcommChannelClosed_(self, _channel: Any) -> None:  # noqa: N802
        self.sink(b"")


def _pump_run_loop() -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0))


class MacRfcommTransport:
    def __init__(self, channel: Any, delegate: Any, incoming: asyncio.Queue[bytes]) -> None:
        self._channel = channel
        self._delegate = delegate
        self._incoming = incoming
        self._pump = asyncio.create_task(self._pump_forever(), name="IOBluetooth run loop")

    @classmethod
    async def open(cls, address: str, channel_id: int = CONTROL_CHANNEL) -> MacRfcommTransport:
        incoming: asyncio.Queue[bytes] = asyncio.Queue()
        device = IOBluetoothDevice.deviceWithAddressString_(address.replace(":", "-"))
        if device is None:
            raise OSError(f"no Bluetooth device with address {address}")
        delegate = _ChannelDelegate.alloc().initWithSink_(incoming.put_nowait)
        rc = _IO_SUCCESS
        # Reopening right after a close fails with kIOReturnError until the receiver releases the channel.
        for attempt in range(_OPEN_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_OPEN_RETRY_DELAY)
            rc, channel = device.openRFCOMMChannelSync_withChannelID_delegate_(None, channel_id, delegate)
            if rc == _IO_SUCCESS and channel is not None:
                return cls(channel, delegate, incoming)
            log.debug("RFCOMM open attempt %d failed: IOReturn %#x", attempt + 1, rc & 0xFFFFFFFF)
        raise OSError(f"failed to open RFCOMM channel {channel_id}: IOReturn {rc & 0xFFFFFFFF:#x}")

    async def _pump_forever(self) -> None:
        while True:
            _pump_run_loop()
            await asyncio.sleep(_PUMP_INTERVAL)

    async def read(self) -> bytes:
        data = await self._incoming.get()
        if not data:
            raise ConnectionError("RFCOMM channel closed by the receiver")
        return data

    async def write(self, data: bytes) -> None:
        rc = self._channel.writeSync_length_(data, len(data))
        if rc != _IO_SUCCESS:
            raise OSError(f"RFCOMM write failed: IOReturn {rc & 0xFFFFFFFF:#x}")

    async def close(self) -> None:
        self._pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._pump
        self._channel.closeChannel()
