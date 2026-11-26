<p align="center"><img src="assets/badge.png" alt="" width="256"></p>

# Denon 500 Series for Home Assistant

Home Assistant integration that controls a Denon AVR-S510BT (and probably the
other "500 Series Remote" models) over the receiver's Bluetooth serial port.
It provides a media player plus tuner, Quick Select and sound mode controls.
The protocol is documented in [PROTOCOL.md](PROTOCOL.md).

## How it works

The integration keeps one RFCOMM connection to SPP channel 2 open from inside
Home Assistant, using a standard library socket; no add-on or broker. It
updates state from what the receiver pushes, without polling. If the link
drops, it reconnects with backoff (5 s up to 5 min). Scanning and pairing talk
to BlueZ over D-Bus with `dbus-fast`, which Home Assistant already ships.

## What you get

| Remote button | In Home Assistant |
|---|---|
| POWER | Media player on/off |
| Input buttons | Media player source list |
| VOLUME, MUTE | Media player volume and mute. The receiver only steps volume, so setting a level steps toward it. |
| QUICK SELECT 1-4 | Quick Select 1-4 buttons |
| SOUND MODE | Sound mode button (steps to the next mode); the current mode shows on the media player |
| CHANNEL +/- | Media player next/previous on FM/AM |
| MEMORY | `denon_500_series.store_preset` action: saves the playing station to preset 1-56 |
| MODE | Tuning mode select (auto/manual), available on FM/AM |
| ⏮ ⏯ ⏭ | Media player previous, play/pause and next on USB |

On FM/AM the media player also shows the station and preset number, and a
Tuner preset select recalls any of the 56 presets by frequency.

Confirmed on an AVR-S510BT: reading power, input, volume and the tuner
station, and stepping volume. The other commands are taken from the official
app and not yet confirmed; debug logging shows what is sent and received.

Not available over Bluetooth: TUNE +/-, SLEEP, ECO, CH LEVEL, RESTORER,
AUDIO DELAY, SEARCH and the menu buttons (SETUP, OPTION, INFO, BACK, arrows,
ENTER). The official app doesn't send them either. Input names are fixed;
names set on the receiver aren't reported.

The integration shows as unavailable while the Bluetooth link is down and
reconnects on its own. Settings → Devices & services → the integration →
Download diagnostics gives the current receiver state for bug reports.

## Requirements

- Home Assistant 2026.11 or newer, on HA OS or the Container install. Container
  needs `--network=host`, `--privileged` and `-v /run/dbus:/run/dbus:ro`, as in
  the official install docs. Bluetooth sockets only work in the host network
  namespace.
- A Bluetooth adapter that supports Classic Bluetooth (BR/EDR), within range of
  the receiver. BLE-only adapters won't work.
- No Bluetooth audio service on the host (PipeWire or PulseAudio with Bluetooth
  support). If one is present, it may connect audio to the receiver, and the
  receiver then switches its input to Bluetooth.
- On the receiver: Setup → General → BluetoothStandby → On. Without it the
  receiver turns Bluetooth off in standby and can't be woken. The integration
  refuses to power off unless this setting is confirmed on.

## Setup

1. **Install.** Settings → Marketplace → Add from link →
   `nburns/denon-500-series-home-assistant` → Install. The setup form opens
   straight away. You can also copy `custom_components/denon_500_series` into
   your HA config's `custom_components/` and restart.
2. **Add the receiver.** The form offers three buttons:
   - **Scan for receivers**: turn the receiver on and hold BLUETOOTH on its
     remote for about 3 seconds (pairing mode) first. The scan stops as soon as
     a receiver shows up, or after a minute. Picking the receiver from the
     results pairs it with the host.
   - **Choose a paired device**: shown when the host already has paired
     Classic Bluetooth devices.
   - **Enter a Bluetooth address**: for a receiver that is already paired.

Pairing from the form needs no PIN (the S510BT shows none). If BlueZ forgot
the receiver since the scan, pairing scans for it again first. If a receiver
does ask for a PIN, "0000" is sent. If that fails, pair it with `bluetoothctl`
instead, then pick it from the list. `bluetoothctl` is available in the Terminal & SSH add-on, on the
HA OS console (type `login`), or with `docker exec -it homeassistant
bluetoothctl`:

```
scan on
pair 08:EF:3B:36:B7:FF
scan off
```

### Troubleshooting

Settings → Devices & services → Denon 500 Series → ⋮ → Enable debug logging,
retry, then disable it to download the log. The log shows the adapter used,
every device the scan sees with its signal strength and pairing state, each
BlueZ call and its error, and the device's state after pairing.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run python -m tools.cli status         # talk to the receiver from a Mac or Linux box
uv run python -m tools.cli -v monitor 60  # print state changes, with raw frames
```

On macOS, `/dev/cu.DenonAVR-S510BT` is bound to SPP channel 1, which never
answers. `tools/macos_transport.py` opens channel 2 through IOBluetooth instead.

The integration icon is drawn in `assets/icon.svg` and `assets/dark_icon.svg`;
Home Assistant serves the PNGs in `custom_components/denon_500_series/brand/`.
After editing the SVGs, regenerate them:

```sh
for v in icon dark_icon; do
  rsvg-convert -w 256 -h 256 -o "custom_components/denon_500_series/brand/$v.png" "assets/$v.svg"
  rsvg-convert -w 512 -h 512 -o "custom_components/denon_500_series/brand/$v@2x.png" "assets/$v.svg"
done
rsvg-convert -w 1024 -o assets/badge.png assets/badge.svg
```
