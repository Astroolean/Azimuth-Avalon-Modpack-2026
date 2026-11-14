"""Publisher links and Steam discovery for additional Avalon vehicles."""
from __future__ import annotations

from azimuth_app.core.worlds import World, workshop_paths


# Preferred current publisher items from the retained vehicle investigation.
# Each tuple is Workshop ID, user-facing name, and a short content description.
ITEMS = (
    ("3456445155", "MBM Go-Kart", "Go-kart requested for the next vehicle showcase"),
    ("3827497320", "GolfCart", "Four-seat electric golf cart"),
    ("3830162428", "CrSk Vespa 125 Primavera", "Scooters in clean and rusty finishes"),
    ("3125203834", "Techs ATV", "Quad bikes"),
    ("2880254695", "RaG Vehicle Pack", "Schoolbus, military trucks, off-roaders and ATV"),
    ("3411055412", "MaharlikaPH Cars", "Campers, motorhomes, Cybertruck and other vehicles"),
    ("3387855369", "4KBOSSK Vehicle Package", "Cars, pickups, monster truck, motorhome and Kamaz"),
    ("3354681846", "MaharlikaPH Boats", "Jet ski, yacht, airboat, houseboat, trawler and more"),
    ("2651195301", "Arma 2 Helicopters Remastered", "Additional helicopters using Expansion"),
    ("3011551250", "Trolley Kart UPDATED", "Rideable trolley kart"),
    ("2971993210", "Arma Bicycles", "Additional rideable bicycle models"),
    ("3639695989", "LMs Planes", "Cessna, Tigermoth, seaplane and Spitfire"),
    ("3706032797", "MaharlikaPH Galleon", "Large sailing ship; dedicated water placement needed"),
    ("3810689113", "Airship-Survive", "Airship; dedicated setup needed"),
)
ITEM_IDS = frozenset(row[0] for row in ITEMS)
DOWNLOAD_CATALOG = World(
    "vehicle_downloads", "Additional vehicles", "",
    tuple(row[0] for row in ITEMS), tuple(row[1] for row in ITEMS),
)


def downloaded_ids(server) -> set[str]:
    """Read Steam files in a worker; never enable a pack or alter the world."""
    libraries = server.steam_libraries(server.installation, refresh=True)
    paths = server.worlds.paths("avalon")
    return {
        item_id for item_id, _name, path in workshop_paths(
            DOWNLOAD_CATALOG, paths.mission_source, paths.mods, libraries,
            steam_only=ITEM_IDS,
        ) if path is not None
    }


def resolve(paths, libraries, base_ids):
    """Select installed supported packs in dependency order for both runtimes.

    Missing optional downloads never become missing required-pack errors.
    Steam's pending-update state is respected just as it is in the download UI.
    """
    from azimuth_app.transport import additional_vehicles

    discovered = {
        identity: path for identity, _name, path in workshop_paths(
            DOWNLOAD_CATALOG, paths.mission_source, paths.mods, libraries,
            steam_only=ITEM_IDS,
        ) if path is not None
    }
    selected = {}
    available = set(base_ids)
    pending = {identity: path for identity, path in discovered.items()
               if identity in additional_vehicles.READY_IDS}
    while pending:
        advanced = False
        for identity, path in tuple(pending.items()):
            required = set(additional_vehicles.REQUIRED_DEPENDENCIES.get(identity, ()))
            if required <= available:
                selected[identity] = path
                available.add(identity)
                del pending[identity]
                advanced = True
        if not advanced:
            break
    names = {identity: title for identity, title, _ in ITEMS}
    notices = []
    for identity in pending:
        missing = set(additional_vehicles.REQUIRED_DEPENDENCIES.get(identity, ())) - available
        notices.append(f"{names[identity]} is downloaded but needs its dependencies loaded: "
                       + ", ".join(sorted(missing)) + ".")
    for identity in discovered.keys() - additional_vehicles.READY_IDS:
        notices.append(f"{names[identity]} is downloaded; its installed vehicle definitions could not be read, "
                       "so it has not been added to this launch.")
    return selected, tuple(notices)
