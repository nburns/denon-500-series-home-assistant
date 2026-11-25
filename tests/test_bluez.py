from dbus_fast import Variant

from custom_components.denon_500_series.bluez import ClassicDevice, parse_managed_objects


def _device(address: str, alias: str, *, paired: bool, classic: bool) -> dict[str, dict[str, Variant]]:
    props = {"Address": Variant("s", address), "Alias": Variant("s", alias), "Paired": Variant("b", paired)}
    if classic:
        props["Class"] = Variant("u", 0x240414)
    return {"org.bluez.Device1": props}


def test_lists_classic_devices_paired_then_receivers_first() -> None:
    objects: dict[str, dict[str, dict[str, Variant]]] = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {}},
        "/org/bluez/hci0/dev_A": _device("AA:AA:AA:AA:AA:AA", "Keyboard", paired=True, classic=True),
        "/org/bluez/hci0/dev_B": _device("08:EF:3B:36:B7:FF", "Denon AVR-S510BT", paired=True, classic=True),
        "/org/bluez/hci0/dev_C": _device("CC:CC:CC:CC:CC:CC", "BLE sensor", paired=True, classic=False),
        "/org/bluez/hci0/dev_D": _device("DD:DD:DD:DD:DD:DD", "Neighbour TV", paired=False, classic=True),
    }
    assert parse_managed_objects(objects) == [
        ClassicDevice("08:EF:3B:36:B7:FF", "Denon AVR-S510BT", paired=True),
        ClassicDevice("AA:AA:AA:AA:AA:AA", "Keyboard", paired=True),
        ClassicDevice("DD:DD:DD:DD:DD:DD", "Neighbour TV", paired=False),
    ]


def test_recognizes_receivers_by_address_prefix_like_the_app() -> None:
    assert ClassicDevice("00:1F:47:12:34:56", "00-1F-47-12-34-56", paired=False).looks_like_receiver
    assert not ClassicDevice("A0:E9:DB:01:EE:A3", "Philips SPA4270", paired=False).looks_like_receiver
