import asyncio
from collections.abc import Callable

import pytest

from custom_components.denon_500_series import protocol
from custom_components.denon_500_series.connection import NotConnectedError, ReceiverConnection, Transport
from custom_components.denon_500_series.protocol import Frame, FrameDecoder, Source, SourceCommand


class SimulatedReceiver:
    """Answers the way the real receiver did in live testing."""

    def __init__(self, *, standby_bt: bool = True, volume_raw: int = 0x4D, volume_step: int = 1) -> None:
        self.power = True
        self.standby_bt = standby_bt
        self.source = Source.BLUETOOTH
        self.volume_raw = volume_raw
        self.volume_step = volume_step
        # FM presets 1-3 hold 87.5, 89.1 and 101.1 MHz; the receiver plays one of them.
        self.presets = [0, 32, 272]
        self.preset = 2
        self.received: list[Frame] = []

    def _frequency(self) -> Frame:
        mhz_khz = round((87.5 + 0.05 * self.presets[self.preset - 1]) * 1000)
        return Frame(Source.FM, 0x31, bytes([1, self.preset]) + f"{mhz_khz:06d}".encode())

    def handle(self, frame: Frame) -> list[Frame]:
        self.received.append(frame)
        g, s = Source.GENERAL, self.source
        match (frame.major, frame.minor, frame.payload):
            case (0, 0x0A, b"\x02"):
                return [Frame(g, 0x0A, bytes([self.power]))]
            case (0, 0x0A, p):
                self.power = p == b"\x01"
                return [Frame(g, 0x0A, p)]
            case (0, 0x09, b"\x02"):
                return [Frame(g, 0x09, bytes([self.standby_bt]))]
            case (0, 0x0B, b""):
                return [Frame(g, 0x0B, bytes([s]))]
            case (0, 0x04, b""):
                return [Frame(g, 0x04, bytes.fromhex("07 40 81 55 82 52 53 54 56 57"))]
            case (0, 0x01, p):
                self.source = Source(p[0])
                if self.source is Source.FM:
                    table = bytes([10]) + b"".join(bytes([v >> 8, v & 0xFF]) for v in self.presets)
                    return [Frame(g, 0x01, p), Frame(Source.FM, 0x32, table)]
                return [Frame(g, 0x01, p)]
            case (0x81, 0x31, b""):
                return [self._frequency()]
            case (0x81, 0x07 | 0x08, b""):
                self.preset = self.preset % len(self.presets) + 1 if frame.minor == 0x07 else self.preset - 1 or 3
                return []
            case (0x81, 0x38, p):
                self.preset = p[0]
                return []
            case (major, SourceCommand.VOLUME_UP | SourceCommand.VOLUME_DOWN, b"") if major == s:
                self.volume_raw += self.volume_step if frame.minor == SourceCommand.VOLUME_UP else -self.volume_step
                return [Frame(s, 0x02, bytes([0xC5, self.volume_raw, 0]))]
            case _:
                return []


class FakeTransport:
    def __init__(self, receiver: SimulatedReceiver) -> None:
        self._receiver = receiver
        self._decoder = FrameDecoder()
        self._incoming: asyncio.Queue[bytes] = asyncio.Queue()

    async def read(self) -> bytes:
        data = await self._incoming.get()
        if not data:
            raise ConnectionError("link dropped")
        return data

    async def write(self, data: bytes) -> None:
        for frame in self._decoder.feed(data):
            for reply in self._receiver.handle(frame):
                self._incoming.put_nowait(reply.encode())

    def drop(self) -> None:
        self._incoming.put_nowait(b"")

    async def close(self) -> None:
        pass


async def _connect(receiver: SimulatedReceiver) -> tuple[ReceiverConnection, list[FakeTransport]]:
    transports: list[FakeTransport] = []

    async def factory() -> Transport:
        transport = FakeTransport(receiver)
        transports.append(transport)
        return transport

    rx = ReceiverConnection(factory, reconnect_min=0.01)
    rx.start()
    await rx.wait_connected(1)
    for _ in range(50):
        if rx.state.supported_sources is not None:
            break
        await asyncio.sleep(0.01)
    return rx, transports


async def test_initial_queries_populate_state() -> None:
    rx, _ = await _connect(SimulatedReceiver())
    try:
        s = rx.state
        assert (s.connected, s.power, s.bluetooth_standby, s.source) == (True, True, True, Source.BLUETOOTH)
        assert s.supported_sources == (
            Source.BLUETOOTH,
            Source.USB1,
            Source.FM,
            Source.BLURAY,
            Source.AM,
            Source.CBL_SAT,
            Source.DVD_BLURAY,
            Source.MEDIA_PLAYER,
            Source.GAME,
            Source.TV_AUDIO,
        )
    finally:
        await rx.stop()


async def test_set_volume_seeks_by_stepping() -> None:
    receiver = SimulatedReceiver(volume_raw=0x4D)
    rx, _ = await _connect(receiver)
    try:
        volume = await rx.set_volume_raw(0x50)
        assert volume.raw == 0x50 == receiver.volume_raw
        assert rx.state.volume == volume
    finally:
        await rx.stop()


def _volume_presses(receiver: SimulatedReceiver) -> int:
    steps = {SourceCommand.VOLUME_UP, SourceCommand.VOLUME_DOWN}
    return sum(1 for f in receiver.received if f.major == receiver.source and f.minor in steps)


async def test_set_volume_stops_when_a_press_passes_the_target() -> None:
    receiver = SimulatedReceiver(volume_raw=0x40, volume_step=2)
    rx, _ = await _connect(receiver)
    try:
        volume = await rx.set_volume_raw(0x45)
        assert volume.raw == receiver.volume_raw == 0x46
        # One press down to learn the level (0x3E), then four up; no back-and-forth around 0x45.
        assert _volume_presses(receiver) == 5
    finally:
        await rx.stop()


async def test_set_volume_follows_the_latest_target_during_a_drag() -> None:
    receiver = SimulatedReceiver(volume_raw=0x40)
    rx, _ = await _connect(receiver)
    try:
        results = await asyncio.gather(*(rx.set_volume_raw(t) for t in (0x48, 0x50, 0x44)))
        assert receiver.volume_raw == 0x44
        assert [v.raw for v in results] == [0x44, 0x44, 0x44]
        # One press to learn the level (0x3F), then straight to the last target with no detours.
        assert _volume_presses(receiver) == 6
    finally:
        await rx.stop()


async def test_power_off_refused_without_bluetooth_standby() -> None:
    receiver = SimulatedReceiver(standby_bt=False)
    rx, _ = await _connect(receiver)
    try:
        with pytest.raises(RuntimeError, match="Bluetooth standby"):
            await rx.set_power(False)
        assert receiver.power is True
        assert protocol.power(False) not in receiver.received
    finally:
        await rx.stop()


async def test_reconnects_after_link_drop() -> None:
    receiver = SimulatedReceiver()
    rx, transports = await _connect(receiver)
    try:
        transports[0].drop()
        for _ in range(100):
            if len(transports) == 2 and rx.state.connected and rx.state.power is not None:
                break
            await asyncio.sleep(0.01)
        assert len(transports) == 2
        assert rx.state.connected
        assert rx.state.supported_sources is not None
        await rx.select_source(Source.TV_AUDIO)
    finally:
        await rx.stop()
    with pytest.raises(NotConnectedError):
        await rx.select_source(Source.TV_AUDIO)


async def _until(condition: Callable[[], bool]) -> None:
    for _ in range(100):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


async def test_tuner_station_and_presets_follow_the_receiver() -> None:
    receiver = SimulatedReceiver()
    rx, _ = await _connect(receiver)
    try:
        await rx.select_source(Source.FM)
        await _until(lambda: rx.state.tuner_station is not None and rx.state.presets is not None)
        assert (rx.state.tuner_station, rx.state.tuner_preset) == ("FM 89.10 MHz", 2)
        assert [p.label for p in rx.state.presets or ()] == ["FM 87.50 MHz", "FM 89.10 MHz", "FM 101.10 MHz"]

        await rx.next_track()
        await _until(lambda: rx.state.tuner_preset == 3)
        assert rx.state.tuner_station == "FM 101.10 MHz"

        await rx.recall_preset(1)
        await _until(lambda: rx.state.tuner_preset == 1)
        assert rx.state.tuner_station == "FM 87.50 MHz"

        await rx.select_source(Source.BLUETOOTH)
        await _until(lambda: rx.state.source is Source.BLUETOOTH)
        assert rx.state.tuner_station is None
    finally:
        await rx.stop()


async def test_track_and_tuner_commands_refuse_other_inputs() -> None:
    rx, _ = await _connect(SimulatedReceiver())
    try:
        with pytest.raises(RuntimeError):
            await rx.next_track()
        with pytest.raises(RuntimeError):
            await rx.recall_preset(1)
        with pytest.raises(RuntimeError):
            await rx.play()
    finally:
        await rx.stop()
