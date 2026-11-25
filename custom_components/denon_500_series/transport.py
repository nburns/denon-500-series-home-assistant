"""Linux RFCOMM transport using the kernel's Bluetooth sockets (BlueZ); stdlib only."""

from __future__ import annotations

import asyncio
import socket

# The receiver exposes two SPP channels; only channel 2 ("BT SPP 2") carries the control protocol.
CONTROL_CHANNEL = 2
CONNECT_TIMEOUT = 15.0
_READ_SIZE = 1024


class RfcommTransport:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def open(cls, address: str, channel: int = CONTROL_CHANNEL) -> RfcommTransport:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, (address, channel)), CONNECT_TIMEOUT)
            reader, writer = await asyncio.open_connection(sock=sock)
        except BaseException:
            sock.close()
            raise
        return cls(reader, writer)

    async def read(self) -> bytes:
        data = await self._reader.read(_READ_SIZE)
        if not data:
            raise ConnectionError("RFCOMM link closed by the receiver")
        return data

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def close(self) -> None:
        self._writer.close()
        await self._writer.wait_closed()
