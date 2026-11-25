"""Dev CLI: drives the integration's ReceiverConnection directly, outside Home Assistant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from custom_components.denon_500_series import protocol
from custom_components.denon_500_series.connection import ReceiverConnection, ReceiverState, Transport
from custom_components.denon_500_series.const import SOURCE_LABELS
from custom_components.denon_500_series.protocol import Source

DEFAULT_ADDRESS = "08:EF:3B:36:B7:FF"
CONNECT_TIMEOUT = 20.0
SETTLE_TIME = 1.5

_SOURCE_NAMES = {s.name.lower().replace("_", "-"): s for s in Source if s is not Source.GENERAL}


def _on_off(value: str) -> bool:
    if value in ("on", "off"):
        return value == "on"
    raise argparse.ArgumentTypeError(f"expected on/off, got {value!r}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="denon-500-series", description="Control the receiver over Bluetooth serial.")
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("-v", "--verbose", action="store_true", help="log raw frames")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="state reported after connecting")
    p = sub.add_parser("power", help="set power")
    p.add_argument("state", type=_on_off)
    p = sub.add_parser("bluetooth-standby", help="set the receiver's Bluetooth standby setting")
    p.add_argument("state", type=_on_off)
    p = sub.add_parser("source", help="select the input source")
    p.add_argument("name", choices=sorted(_SOURCE_NAMES))
    for name in ("up", "down"):
        p = sub.add_parser(name, help=f"volume {name}")
        p.add_argument("steps", nargs="?", type=int, default=1)
    p = sub.add_parser("volume", help="seek to an absolute level on the receiver's 0-98 display scale")
    p.add_argument("level", type=float)
    p = sub.add_parser("mute", help="set mute")
    p.add_argument("state", type=_on_off)
    sub.add_parser("sound-mode", help="press the app's Sound Mode button (behaviour unverified)")
    p = sub.add_parser("monitor", help="print state changes")
    p.add_argument("seconds", nargs="?", type=float, default=float("inf"))
    return parser


async def _open_transport(address: str) -> Transport:
    if sys.platform == "darwin":
        from tools.macos_transport import MacRfcommTransport

        return await MacRfcommTransport.open(address)
    from custom_components.denon_500_series.transport import RfcommTransport

    return await RfcommTransport.open(address)


def _print_state(state: ReceiverState) -> None:
    source = SOURCE_LABELS.get(state.source) if state.source is not None else None
    print(
        f"connected={state.connected} power={state.power} standby_bt={state.bluetooth_standby} "
        f"source={source} volume={state.volume.display if state.volume else None} "
        f"muted={state.muted} sound_mode={state.sound_mode!r}",
        flush=True,
    )


async def _run(args: argparse.Namespace, rx: ReceiverConnection) -> None:
    match args.command:
        case "status":
            await asyncio.sleep(SETTLE_TIME)
            _print_state(rx.state)
            if rx.state.supported_sources is not None:
                print("supported:", ", ".join(SOURCE_LABELS[s] for s in rx.state.supported_sources))
        case "power":
            await rx.set_power(args.state)
        case "bluetooth-standby":
            await rx.set_bluetooth_standby(args.state)
        case "source":
            await rx.select_source(_SOURCE_NAMES[args.name])
        case "up" | "down":
            for _ in range(args.steps):
                volume = await rx.volume_step(args.command == "up")
                print(volume.display)
        case "volume":
            print((await rx.set_volume_raw(round(args.level * 2))).display)
        case "mute":
            await rx.set_mute(args.state)
        case "sound-mode":
            print(await rx.press_sound_mode())
        case "monitor":
            rx.add_listener(_print_state)
            await asyncio.sleep(args.seconds)
        case _:
            raise AssertionError(f"unhandled command {args.command}")


async def _main(args: argparse.Namespace) -> None:
    async def factory() -> Transport:
        return await _open_transport(args.address)

    rx = ReceiverConnection(factory, reconnect_min=1.0)
    rx.start()
    try:
        await rx.wait_connected(CONNECT_TIMEOUT)
        await asyncio.sleep(0.5)
        await _run(args, rx)
        # Give the last command time to reach the receiver before the link closes.
        await asyncio.sleep(0.3)
    finally:
        await rx.stop()


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        asyncio.run(_main(args))
    except (TimeoutError, ConnectionError, RuntimeError, protocol.ProtocolError) as e:
        print(f"denon-500-series: {e!r}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
