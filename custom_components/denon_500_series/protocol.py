"""Wire format of the receiver's Bluetooth serial (SPP) control channel.

Every message, in both directions, is framed as::

    'A' 'T' | major | minor | len | payload[len] | checksum

``major`` is the source the message concerns (0x00 for general/system
messages), ``minor`` is the command, and
``checksum = -(len + sum(payload)) & 0xFF``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum

log = logging.getLogger(__name__)

HEADER = b"AT"
_HEADER_LEN = len(HEADER) + 3
_MAX_SCAN_BUFFER = 64 * 1024


class ProtocolError(Exception):
    pass


class Source(IntEnum):
    GENERAL = 0x00
    BLUETOOTH = 0x07
    STREAMING = 0x08
    CD = 0x10
    IPOD = 0x20
    SD = 0x30
    USB1 = 0x40
    USB2 = 0x41
    AUX = 0x50
    AUX_MIC = 0x51
    CBL_SAT = 0x52
    DVD_BLURAY = 0x53
    MEDIA_PLAYER = 0x54
    BLURAY = 0x55
    GAME = 0x56
    TV_AUDIO = 0x57
    PORTABLE = 0x60
    TAPE = 0x70
    FM = 0x81
    AM = 0x82
    DAB = 0x83
    AIRPLAY = 0x90
    ANDROID = 0xA0
    DMR = 0xB0


TUNER_SOURCES = frozenset({Source.FM, Source.AM, Source.DAB})
_SOURCE_CODES = frozenset(s.value for s in Source)


class GeneralCommand(IntEnum):
    """Minor codes used with ``major == Source.GENERAL``."""

    SELECT_SOURCE = 0x01
    SUPPORTED_SOURCES = 0x04
    SUPPORTED_FEATURES = 0x06
    STORAGE_STATE = 0x07
    BLUETOOTH_STANDBY = 0x09
    POWER = 0x0A
    CURRENT_SOURCE = 0x0B
    A2DP_PROFILE = 0x0D


class SourceCommand(IntEnum):
    """Minor codes used with ``major`` set to the active source."""

    VOLUME_UP = 0x00
    VOLUME_DOWN = 0x01
    VOLUME = 0x02
    FEATURE_STATE = 0x03
    MUTE = 0x1D
    SOUND_MODE = 0x1E


QUERY = 0x02


def checksum(payload: bytes) -> int:
    return -(len(payload) + sum(payload)) & 0xFF


@dataclass(frozen=True)
class Frame:
    major: int
    minor: int
    payload: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.major <= 0xFF or not 0 <= self.minor <= 0xFF:
            raise ValueError(f"major/minor out of byte range: {self.major:#x}/{self.minor:#x}")
        if len(self.payload) > 0xFF:
            raise ValueError(f"payload too long: {len(self.payload)} bytes")

    def encode(self) -> bytes:
        body = bytes([self.major, self.minor, len(self.payload)]) + self.payload
        return HEADER + body + bytes([checksum(self.payload)])

    def __str__(self) -> str:
        return f"AT {self.major:02X} {self.minor:02X} [{self.payload.hex(' ')}]"


class FrameDecoder:
    """Incremental decoder: feed raw bytes, get complete, checksum-valid frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        frames: list[Frame] = []
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                # Keep a trailing 'A' in case the matching 'T' arrives next read.
                keep = 1 if self._buf.endswith(HEADER[:1]) else 0
                self._discard(len(self._buf) - keep)
                return frames
            if start > 0:
                self._discard(start)
            if len(self._buf) < _HEADER_LEN:
                return frames
            length = self._buf[4]
            total = _HEADER_LEN + length + 1
            if len(self._buf) < total:
                if len(self._buf) > _MAX_SCAN_BUFFER:
                    raise ProtocolError("receive buffer overflow while waiting for frame body")
                return frames
            payload = bytes(self._buf[_HEADER_LEN : _HEADER_LEN + length])
            received = self._buf[total - 1]
            if received != checksum(payload):
                # Likely a false 'AT' match inside another frame's payload: resync one byte on.
                log.warning(
                    "checksum mismatch (got %#04x, want %#04x), resyncing: %s",
                    received,
                    checksum(payload),
                    bytes(self._buf[:total]).hex(" "),
                )
                self._discard(1)
                continue
            frames.append(Frame(self._buf[2], self._buf[3], payload))
            del self._buf[:total]

    def _discard(self, count: int) -> None:
        if count > 0:
            log.debug("discarding %d unframed bytes: %s", count, bytes(self._buf[:count]).hex(" "))
            del self._buf[:count]


def power(on: bool) -> Frame:
    return Frame(Source.GENERAL, GeneralCommand.POWER, bytes([int(on)]))


def query_power() -> Frame:
    return Frame(Source.GENERAL, GeneralCommand.POWER, bytes([QUERY]))


def bluetooth_standby(enabled: bool) -> Frame:
    return Frame(Source.GENERAL, GeneralCommand.BLUETOOTH_STANDBY, bytes([int(enabled)]))


def query_bluetooth_standby() -> Frame:
    return Frame(Source.GENERAL, GeneralCommand.BLUETOOTH_STANDBY, bytes([QUERY]))


def query_current_source() -> Frame:
    return Frame(Source.GENERAL, GeneralCommand.CURRENT_SOURCE)


def query_supported_sources() -> Frame:
    return Frame(Source.GENERAL, GeneralCommand.SUPPORTED_SOURCES)


def select_source(source: Source) -> Frame:
    if source is Source.GENERAL:
        raise ValueError("GENERAL is not a selectable source")
    return Frame(Source.GENERAL, GeneralCommand.SELECT_SOURCE, bytes([source]))


def volume_up(active: Source) -> Frame:
    return Frame(active, SourceCommand.VOLUME_UP)


def volume_down(active: Source) -> Frame:
    return Frame(active, SourceCommand.VOLUME_DOWN)


def mute(active: Source, on: bool) -> Frame:
    return Frame(active, SourceCommand.MUTE, bytes([int(on)]))


def sound_mode(active: Source) -> Frame:
    """The app's "Sound Mode" button. The receiver answers with the mode name."""
    return Frame(active, SourceCommand.SOUND_MODE)


def query_feature_state(active: Source) -> Frame:
    return Frame(active, SourceCommand.FEATURE_STATE)


@dataclass(frozen=True)
class Volume:
    raw: int
    max_raw: int
    relative: bool | None
    """True: shown in dB relative to reference. False: absolute 0-98 scale. None: not reported."""

    @property
    def display(self) -> str:
        if self.relative:
            return "---.-dB" if self.raw == 0 else f"{(self.raw - 160) / 2:.1f}dB"
        return f"{self.raw / 2:.1f}"


@dataclass(frozen=True)
class PowerState:
    on: bool


@dataclass(frozen=True)
class BluetoothStandbyState:
    enabled: bool


@dataclass(frozen=True)
class CurrentSource:
    source: Source


@dataclass(frozen=True)
class SupportedSources:
    sources: tuple[Source, ...]
    unknown_codes: tuple[int, ...]


@dataclass(frozen=True)
class VolumeState:
    source: Source
    volume: Volume


@dataclass(frozen=True)
class MuteState:
    source: Source
    muted: bool


@dataclass(frozen=True)
class SoundModeName:
    source: Source
    name: str


@dataclass(frozen=True)
class FeatureState:
    source: Source
    muted: bool
    volume: Volume
    sound_mode: str


@dataclass(frozen=True)
class Unrecognized:
    frame: Frame


Event = (
    PowerState
    | BluetoothStandbyState
    | CurrentSource
    | SupportedSources
    | VolumeState
    | MuteState
    | SoundModeName
    | FeatureState
    | Unrecognized
)


def _source(code: int) -> Source:
    try:
        return Source(code)
    except ValueError:
        raise ProtocolError(f"unknown source code {code:#04x}") from None


def _on_off(frame: Frame) -> bool:
    if len(frame.payload) < 1 or frame.payload[0] not in (0, 1):
        raise ProtocolError(f"expected 0/1 state byte: {frame}")
    return frame.payload[0] == 1


def _text(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").strip(" \x00")


def decode(frame: Frame) -> Event:
    p = frame.payload
    if frame.major == Source.GENERAL:
        match frame.minor:
            case GeneralCommand.POWER:
                return PowerState(_on_off(frame))
            case GeneralCommand.BLUETOOTH_STANDBY:
                return BluetoothStandbyState(_on_off(frame))
            case GeneralCommand.SELECT_SOURCE | GeneralCommand.CURRENT_SOURCE if len(p) == 1:
                return CurrentSource(_source(p[0]))
            case GeneralCommand.SUPPORTED_SOURCES:
                known = tuple(Source(c) for c in p if c in _SOURCE_CODES and c != Source.GENERAL)
                unknown = tuple(c for c in p if c not in _SOURCE_CODES)
                return SupportedSources(known, unknown)
            case _:
                return Unrecognized(frame)

    try:
        source = Source(frame.major)
    except ValueError:
        return Unrecognized(frame)

    match frame.minor:
        case SourceCommand.VOLUME if len(p) in (2, 3):
            relative = bool(p[2]) if len(p) == 3 else None
            return VolumeState(source, Volume(raw=p[1], max_raw=p[0], relative=relative))
        case SourceCommand.MUTE if len(p) == 1:
            return MuteState(source, p[0] != 0)
        case SourceCommand.SOUND_MODE if len(p) >= 1:
            return SoundModeName(source, _text(p[1:]))
        case SourceCommand.FEATURE_STATE if len(p) >= 17 and source not in TUNER_SOURCES:
            return FeatureState(
                source,
                muted=bool(p[1] & 1),
                volume=Volume(raw=p[3], max_raw=p[2], relative=bool(p[11])),
                sound_mode=_text(p[17:]),
            )
        case _:
            return Unrecognized(frame)
