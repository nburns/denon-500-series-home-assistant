# Denon 500 Series for Home Assistant

Home Assistant integration that controls a Denon AVR-S510BT (and probably the
other "500 Series Remote" models) over the receiver's Bluetooth serial port.
It provides a `media_player` with power, volume, mute and source selection. The
protocol is documented in [PROTOCOL.md](PROTOCOL.md).

## How it works

The integration keeps one RFCOMM connection to SPP channel 2 open from inside
Home Assistant. It uses only the Python standard library (no add-on, broker or
extra packages) and updates state from what the receiver pushes, without
polling. If the link drops, it reconnects with backoff (5 s up to 5 min).

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
2. **Pick or pair the receiver.** The form lists the Classic Bluetooth devices
   already paired with the host, Denon receivers first. To pair a new one, turn
   the receiver on, press and hold BLUETOOTH on its remote for about 3 seconds
   (pairing mode), then choose "Scan for devices". The scan stops as soon as a
   receiver shows up, or after a minute. The receiver then appears as "pair
   now", and picking it pairs it before setup finishes. You can also type an
   address.

Pairing from the form uses Bluetooth's Just Works method, with no PIN. If the
receiver insists on a PIN, pair it with `bluetoothctl` instead, then pick it
from the list. `bluetoothctl` is available in the Terminal & SSH add-on, on the
HA OS console (type `login`), or with `docker exec -it homeassistant
bluetoothctl`:

```
scan on
pair 08:EF:3B:36:B7:FF
scan off
```

You get one media player with power, volume, mute and input selection. It
shows as unavailable while the Bluetooth link is down and reconnects on its
own. Settings → Devices & services → the integration → Download diagnostics
gives the current receiver state for bug reports.

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
