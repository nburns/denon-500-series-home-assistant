from .protocol import Source

DOMAIN = "denon_500_series"

SOURCE_LABELS: dict[Source, str] = {
    Source.BLUETOOTH: "Bluetooth",
    Source.STREAMING: "Internet Streaming",
    Source.CD: "CD",
    Source.IPOD: "iPod",
    Source.SD: "SD",
    Source.USB1: "USB1",
    Source.USB2: "USB2",
    Source.AUX: "AUX",
    Source.AUX_MIC: "AUX-MIC",
    Source.CBL_SAT: "CBL/SAT",
    Source.DVD_BLURAY: "DVD/Blu-ray",
    Source.MEDIA_PLAYER: "Media Player",
    Source.BLURAY: "Blu-ray",
    Source.GAME: "Game",
    Source.TV_AUDIO: "TV Audio",
    Source.PORTABLE: "Portable",
    Source.TAPE: "TAPE",
    Source.FM: "FM",
    Source.AM: "AM",
    Source.DAB: "DAB",
    Source.AIRPLAY: "AirPlay",
    Source.ANDROID: "Android",
    Source.DMR: "DMR",
}
