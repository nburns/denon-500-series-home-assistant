import pytest

from custom_components.denon_500_series import protocol
from custom_components.denon_500_series.protocol import Frame, FrameDecoder, Source

# Frames captured from the receiver during the initial probe.
VOLUME_49 = bytes.fromhex("41 54 52 02 03 c5 62 00 d6")
SOUND_MODE_STEREO = b"AT\x52\x1e\x11\x00     Stereo     " + bytes([protocol.checksum(b"\x00     Stereo     ")])


def test_encode_matches_app_byte_layout() -> None:
    assert protocol.power(True).encode() == bytes.fromhex("41 54 00 0a 01 01 fe")
    assert protocol.volume_up(Source.CBL_SAT).encode() == bytes.fromhex("41 54 52 00 00 00")
    assert protocol.select_source(Source.TV_AUDIO).encode() == bytes.fromhex("41 54 00 01 01 57 a8")


def test_decoder_handles_noise_split_reads_and_multiple_frames() -> None:
    decoder = FrameDecoder()
    stream = b"\x00\xffA" + VOLUME_49 + SOUND_MODE_STEREO
    frames = [f for i in range(0, len(stream), 4) for f in decoder.feed(stream[i : i + 4])]
    assert frames == [
        Frame(0x52, 0x02, bytes.fromhex("c5 62 00")),
        Frame(0x52, 0x1E, b"\x00     Stereo     "),
    ]


def test_decoder_resyncs_after_bad_checksum() -> None:
    corrupt = VOLUME_49[:-1] + b"\x00"
    assert FrameDecoder().feed(corrupt + VOLUME_49) == [Frame(0x52, 0x02, bytes.fromhex("c5 62 00"))]


def test_decode_volume_and_sound_mode() -> None:
    (vol, mode) = FrameDecoder().feed(VOLUME_49 + SOUND_MODE_STEREO)
    volume = protocol.decode(vol)
    assert isinstance(volume, protocol.VolumeState)
    assert volume.source is Source.CBL_SAT
    assert volume.volume.display == "49.0"
    assert protocol.decode(mode) == protocol.SoundModeName(Source.CBL_SAT, "Stereo")


@pytest.mark.parametrize(("raw", "display"), [(0x8C, "-10.0dB"), (0, "---.-dB")])
def test_relative_volume_display(raw: int, display: str) -> None:
    assert protocol.Volume(raw=raw, max_raw=0xC5, relative=True).display == display


def test_decode_general_replies() -> None:
    assert protocol.decode(Frame(0, 0x0A, b"\x01")) == protocol.PowerState(on=True)
    assert protocol.decode(Frame(0, 0x09, b"\x00")) == protocol.BluetoothStandbyState(enabled=False)
    assert protocol.decode(Frame(0, 0x0B, b"\x52")) == protocol.CurrentSource(Source.CBL_SAT)
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(Frame(0, 0x0A, b"\x05"))


def test_decode_feature_state() -> None:
    payload = bytes([0x00, 0x01, 0xC5, 0x62]) + bytes(7) + b"\x00" + bytes(5) + b"Multi Ch Stereo "
    state = protocol.decode(Frame(0x57, 0x03, payload))
    assert state == protocol.FeatureState(
        Source.TV_AUDIO,
        muted=True,
        volume=protocol.Volume(raw=0x62, max_raw=0xC5, relative=False),
        sound_mode="Multi Ch Stereo",
    )
