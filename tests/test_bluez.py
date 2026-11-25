from dbus_fast import Variant

from custom_components.denon_500_series.bluez import PairedDevice, parse_managed_objects


def _device(address: str, alias: str, *, paired: bool, classic: bool) -> dict[str, dict[str, Variant]]:
    props = {"Address": Variant("s", address), "Alias": Variant("s", alias), "Paired": Variant("b", paired)}
    if classic:
        props["Class"] = Variant("u", 0x240414)
    return {"org.bluez.Device1": props}


def test_lists_paired_classic_devices_receiver_first() -> None:
    objects: dict[str, dict[str, dict[str, Variant]]] = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
        "/org/bluez/hci0/dev_A": _device("AA:AA:AA:AA:AA:AA", "Keyboard", paired=True, classic=True),
        "/org/bluez/hci0/dev_B": _device("08:EF:3B:36:B7:FF", "Denon AVR-S510BT", paired=True, classic=True),
        "/org/bluez/hci0/dev_C": _device("CC:CC:CC:CC:CC:CC", "BLE sensor", paired=True, classic=False),
        "/org/bluez/hci0/dev_D": _device("DD:DD:DD:DD:DD:DD", "Neighbour TV", paired=False, classic=True),
    }
    assert parse_managed_objects(objects) == [
        PairedDevice("08:EF:3B:36:B7:FF", "Denon AVR-S510BT"),
        PairedDevice("AA:AA:AA:AA:AA:AA", "Keyboard"),
    ]
