# AVR-S510BT Bluetooth serial protocol

Recovered by decompiling the "Denon 500 Series Remote" Android app
(`com.dmholdings.denonbtremote` 1.9.0) with jadx, then checked against the
receiver. Nothing here is documented by Denon.

## Transport

The receiver advertises two SPP (UUID 0x1101) records: an unnamed one on RFCOMM
channel 1 and "BT SPP 2" on **channel 2**. Only channel 2 answers; the app's
fallback hard-codes it. macOS binds `/dev/cu.DenonAVR-S510BT` to channel 1, so
that device node stays silent. Right after a close, the receiver refuses a
reopen for about a second.

On connect, the receiver pushes FM/AM tuner tables (`AT 81 32 …`), version
strings (`AT 82 31 …`, `AT 07 31 …`) and `AT 81 40 01 00`. A Bluetooth
connection from a device with an audio profile (e.g. a Mac) switches the input
to Bluetooth.

## Framing

```
'A' 'T' | major | minor | len | payload[len] | checksum
checksum = -(len + sum(payload)) & 0xFF
```

`major` is the **source** the message is about, not a message type. `0x00`
means general/system. Volume, mute and sound-mode messages go to the *active*
source, so the client has to know the current source first (`AT 00 0B 00`).

## Source codes

| code | source        | code | source       |
|------|---------------|------|--------------|
| 0x07 | Bluetooth     | 0x55 | Blu-ray      |
| 0x08 | Bluetooth (alt) | 0x56 | Game       |
| 0x10 | CD            | 0x57 | TV Audio     |
| 0x20 | iPod          | 0x60 | Portable     |
| 0x30 | SD            | 0x70 | Tape         |
| 0x40 | USB1          | 0x81 | FM           |
| 0x41 | USB2          | 0x82 | AM           |
| 0x50 | AUX           | 0x83 | DAB          |
| 0x51 | AUX-MIC       | 0x90 | AirPlay      |
| 0x52 | CBL/SAT       | 0xA0 | Android      |
| 0x53 | DVD/Blu-ray   | 0xB0 | DMR          |
| 0x54 | Media Player  |      |              |

## Commands the app sends

| frame (before checksum)        | meaning |
|--------------------------------|---------|
| `AT 00 0A 01 01` / `.. 00`     | power on / standby |
| `AT 00 0A 01 02`               | query power (sent on connect) |
| `AT 00 09 01 01` / `.. 00`     | Bluetooth standby setting on / off |
| `AT 00 09 01 02`               | query Bluetooth standby (sent on connect) |
| `AT 00 0B 00`                  | query current source (sent after power-on reply) |
| `AT 00 01 01 <src>`            | select source |
| `AT <src> 00 00`               | volume up |
| `AT <src> 01 00`               | volume down |
| `AT <src> 1D 01 <0/1>`         | mute off / on (explicit, not a toggle). No acknowledgement in testing. |
| `AT <src> 1E 00`               | "Sound Mode" button; reply is the mode name. Whether it cycles is unverified. |
| `AT <src> 03 00`               | query feature state. No reply on the Bluetooth input. The app also sends it on tuner inputs (different layout). |
| `AT 00 08 02 00 <1-4>`         | Quick Select 1-4 |
| `AT 00 04 00`                  | query supported sources (reply minor `04`, payload = source codes) |
| `AT 00 06 00`                  | query supported features (reply minor `06`, 65+ bytes; see below) |
| `AT 00 0E 00`                  | query receiver name (reply minor `0E`: encoding byte + name) |
| `AT <tuner> 31 00`             | query tuner frequency |
| `AT 00 0D 09 ...`              | A2DP profile management (phone MAC); not needed for control |

If Bluetooth standby is off, the receiver turns its radio off in standby. The
app refuses to power off in that case and asks the user to enable it first,
because otherwise it could not power the receiver back on. The integration
does the same check.

## Replies / unsolicited status

| minor | payload | meaning |
|-------|---------|---------|
| general `0A` | `<0/1>` | power state |
| general `09` | `<0/1>` | Bluetooth standby |
| general `01`, `0B` | `<src>` | current source |
| src `02` | `max raw [mode]` | volume. mode 0: shown as `raw/2` (0-98 scale); mode != 0: `(raw-160)/2` dB |
| src `1D` | `<0/1>` | mute |
| src `1E` | `00` + 16 ASCII chars | sound mode name, e.g. `"     Stereo     "` |
| src `03` | >= 17 bytes | `[1]&1` mute, `[2]` volume max, `[3]` volume raw, `[11]` volume mode, `[17:]` sound mode name (non-tuner sources) |
| general `04` | source codes | supported sources. S510BT: `07 40 81 55 82 52 53 54 56 57` (no AUX) |
| general `06` | 65+ bytes | feature flags and per-source sound-mode bitmaps; payload byte 45 is the FM step, `'2'` = 50 kHz, `'d'` = 100 kHz |
| general `0E` | `<enc>` + name | receiver name; enc 0 = ASCII. S510BT returned `" DENON AV"` |
| general `07` | `<storage> <state>` | storage state, e.g. `40 00` (USB1, not ready) |
| tuner `31` | `<has preset> <preset> <6 ASCII digits>` | frequency. FM `"089100"` = 89.10 MHz, AM `"000520"` = 520 kHz |
| tuner `32` | `<AM step kHz>` + 56 x 2 bytes | preset table. Per preset: hi bit 7 = AM; value = `(hi & 0x0F) << 8 \| lo`; FM = 87.5 + 0.05 x value MHz (76.0 base in Japan); AM = 520 + 10 x value kHz (522 + 9 x value when step is 9) |
| tuner `40` | bitmask | presets skipped, one bit per block of 8 |
| tuner `04` | `<0/1>` | auto-tuning mode |
| tuner `1D`, `1E`, `02`, `03` | | mute, sound mode name, volume, feature state as for other sources (tuner feature state carries the frequency at payload 11-16 and the sound mode name from 17) |

The early probe captured `AT 52 02 03 C5 62 00` and `AT 52 02 03 C5 4D 00`.
These are volume reports for CBL/SAT (49.0 and 38.5), not sound-mode codes.

## Not exposed by the app

There is no direct sound-mode select, absolute volume set, or setup-menu
access. The app can only do what's listed above.

## Verified live (2026-11-25)

Power query, Bluetooth standby query, current source, and volume up/down with
level replies all work on channel 2. Still unverified: power on/off, source
select, mute, and the sound-mode button.
