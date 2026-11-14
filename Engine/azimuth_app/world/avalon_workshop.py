"""Avalon Workshop manifest and the saved opt-in for this world."""

import json
from pathlib import Path

from azimuth_app.world import avalon_airdrop
from azimuth_app.world import avalon_atmosphere
from azimuth_app.world import avalon_building
from azimuth_app.world import avalon_immersion
from azimuth_app.transport import avalon_land_transport
from azimuth_app.transport import avalon_air_transport
from azimuth_app.transport import avalon_horse_transport
from azimuth_app.transport import avalon_plane_transport
from azimuth_app.core.worlds import atomic_text


ADAPTERS = (avalon_building, avalon_immersion, avalon_atmosphere, avalon_airdrop,
            avalon_land_transport, avalon_air_transport,
            avalon_horse_transport, avalon_plane_transport)
# CF is supplied once by the world's base dependencies. Keep stable order for
# everything else and use each publisher's Workshop ID only once.
ITEMS = tuple(dict(item for module in ADAPTERS for item in module.ITEMS
                   if item[0] != "1559212036").items())
ITEM_IDS = frozenset(item_id for item_id, _ in ITEMS)
MARKER = "avalon-workshop.json"
PACK_VERSION = 3
fishing_setup_pending = avalon_atmosphere.fishing_setup_pending


def version(config: Path) -> int:
    marker = config / MARKER
    if not marker.is_file():
        return 0
    try:
        data = json.loads(marker.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not read Avalon Config/{MARKER}: {error}") from error
    if (not isinstance(data, dict) or set(data) != {"version", "enabled"} or data["enabled"] is not True
            or type(data["version"]) is not int or data["version"] not in (1, 2, PACK_VERSION)):
        raise ValueError(f"Invalid Avalon Config/{MARKER}.")
    return data["version"]


def enabled(config: Path) -> bool:
    return version(config) != 0


def enable(config: Path) -> None:
    config.mkdir(parents=True, exist_ok=True)
    atomic_text(config / MARKER, json.dumps({"version": PACK_VERSION, "enabled": True}, indent=2) + "\n")


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    notices = []
    for module in ADAPTERS:
        result = module.prepare(mission, profiles, mods)
        if result:
            notices.extend(result)
    return tuple(notices)
