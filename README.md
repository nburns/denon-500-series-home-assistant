<p align="center"><img src="assets/badge.png" alt="" width="256"></p>

# Denon 500 Series for Home Assistant

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=nburns&repository=denon-500-series-home-assistant&category=integration)

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

Confirmed on an AVR-S510BT: reading power, input, volume, the tuner station
and the preset list, stepping volume, and powering off. The other commands are
taken from the official app and not yet confirmed; debug logging shows what is
sent and received.

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
- No Bluetooth audio for the receiver on the host. HA OS has no Bluetooth audio
  service, so there is nothing to do. On a Linux desktop running PipeWire or
  PulseAudio, the audio service may connect to the receiver as a speaker, and
  the receiver then switches its input to Bluetooth. The integration only opens
  the control channel and can't prevent this; see
  [Bluetooth audio on the host](#bluetooth-audio-on-the-host).
- On the receiver: Setup → General → BluetoothStandby → On. Without it the
  receiver turns Bluetooth off in standby and can't be woken. The integration
  refuses to power off unless this setting is confirmed on.

## Setup

1. **Install** with the Marketplace button at the top of this page, or
   Settings → Marketplace → Add from link →
   `nburns/denon-500-series-home-assistant`. The setup form opens once it's
   installed.
2. **Add the receiver.** Turn the receiver on and hold BLUETOOTH on its remote
   for about 3 seconds to put it in pairing mode, then choose **Scan for
   receivers** and pick it from the results. No PIN is needed. If the host is
   already paired with it, use **Choose a paired device** instead.

### Troubleshooting

Settings → Devices & services → Denon 500 Series → ⋮ → Enable debug logging,
retry, then disable it to download the log. The log shows the adapter used,
every device the scan sees with its signal strength and pairing state, each
BlueZ call and its error, and the device's state after pairing.

### Bluetooth audio on the host

With PipeWire, a WirePlumber rule (0.5 or newer) keeps it away from the
receiver. Put it in `~/.config/wireplumber/wireplumber.conf.d/denon.conf` (or
the system-wide `/etc/wireplumber/wireplumber.conf.d/`), with the receiver's
address, then restart WirePlumber:

```
monitor.bluez.rules = [
  {
    matches = [ { device.name = "bluez_card.08_EF_3B_36_B7_FF" } ]
    actions = { update-props = { device.disabled = true } }
  }
]
```

With PulseAudio, stop loading `module-bluetooth-discover` (comment it out in
`/etc/pulse/default.pa`). That turns off Bluetooth audio for every device.

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
