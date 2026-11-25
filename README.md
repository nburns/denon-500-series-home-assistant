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

1. **Pair the receiver with the HA host.** Home Assistant's UI can't pair
   Classic Bluetooth devices, so use `bluetoothctl` from one of:
   - the Terminal & SSH add-on
   - the HA OS console (type `login`)
   - `docker exec -it homeassistant bluetoothctl`

   Put the receiver in pairing mode, then run:

   ```
   scan on
   pair 08:EF:3B:36:B7:FF
   trust 08:EF:3B:36:B7:FF
   scan off
   ```

   Stay in the session until pairing finishes, in case it asks for
   confirmation.
2. **Install.** Settings → Marketplace → Add from link →
   `https://github.com/nburns/denon-500-series-home-assistant` → Install. The setup form opens
   straight away. You can also copy `custom_components/denon_500_series` into your HA
   config's `custom_components/` and restart.
3. **Configure.** The form lists the Classic Bluetooth devices paired with the
   host, Denon receivers first. You can also type an address.

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
