"""Avalon building and furniture options and their mission-local setup."""

import json
from pathlib import Path
import re
import stat
from xml.etree import ElementTree

from azimuth_app.economy.avalon_economy import register_bytes
from azimuth_app.economy.avalon_loot_audit import read_economy
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.economy.verified_publisher_loot import verified_rows, ordinary_target, supported_on_map, remembered_rows, correct_two_publisher_placement_gaps
from azimuth_app.economy.publisher_config import decode, DecodeError
from azimuth_app.core.backups import source_type
from azimuth_app.integrations.solitude_signatures import WORKSHOP_ID as SOLITUDE
from azimuth_app.core.worlds import atomic_text

CF = "1559212036"
RAG_CORE = "3556131153"
HUNTING_CABIN = "3117613872"
BASE_BUILDING = "3157695626"
FORTIFICATIONS = "2670506982"
BASE_ITEMS = "2878980498"
BEEHIVE = "2879040969"

ITEMS = (
    (RAG_CORE, "RaG_Core"),
    (HUNTING_CABIN, "RaG_Hunting_Cabin"),
    (BASE_BUILDING, "RaG_BaseBuilding"),
    (BASE_ITEMS, "RaG_BaseItems"),
    (BEEHIVE, "RaG_BeeHive"),
    (SOLITUDE, "Solitude Storage Mod"),
    (FORTIFICATIONS, "Building Fortifications"),
)
EXTRA_ITEMS = ()

# The Hunting Cabin author's setup guide documents the normal kit class and JSON schema.
# https://tyson89.github.io/RaG-Mod-Wikis/mods/hunting-cabin/server-setup-and-class-names/
CABIN_CONFIG = {
    "Version": 1,
    "CanCraftHuntingCabinKit": 1,
    "RuinHammerAfterBuilt": 0,
    "GiveBackMaterialsAfterDismantle": 1,
    "GiveBackKitAfterDismantle": 1,
    "EnableSnowyRoofTexture": 0,
    "NailAmount": 24.0,
    "WoodenPlankAmount": 16.0,
    "WoodenLogAmount": 4.0,
    "StoneAmount": 4.0,
}
CABIN_SETTINGS = Path("RaG_Core", "Configs", "RaG_Hunting_Cabin", "RaG_Hunting_Cabin.json")

# Curated inventory kits only. The other building kits stay craftable.
# https://tyson89.github.io/RaG-Mod-Wikis/mods/basebuilding/building-parts-and-class-names/
# https://tyson89.github.io/RaG-Mod-Wikis/mods/baseitems/key-classnames/
# The Beehive author identifies the deployed kit classname in Bohemia issue T172406.
KIT_SPAWNS = {
    HUNTING_CABIN: ("rag_hunting_cabin_kit",),
    BASE_BUILDING: (
        "RaG_BB_Book", "RaG_BB_FoundationKit", "RaG_BB_FloorKit", "RaG_BB_WallKit",
        "RaG_BB_CeilingKit", "RaG_BB_Single_DoorKit", "RaG_BB_Window_SmallKit",
    ),
    BASE_ITEMS: (
        "rag_baseitems_workbench_kit", "rag_baseitems_workbench_blueprint",
        "rag_baseitems_repair_station_kit", "rag_baseitems_table_saw_static_kit",
        "rag_baseitems_retro_fridge_kit", "rag_baseitems_solarpanel_kit",
        "rag_baseitems_greenhouse_kit", "rag_baseitems_greenhouse_big_kit",
    ),
    BEEHIVE: ("rag_beehive_kit",),
}
# These are the author's exact inventory classes, not inferred display names.
# Both table-saw variants require the blade. Workbench electrical recipes need
# one of the electronics parts; seeding only machine kits leaves those paths dead.
# https://tyson89.github.io/RaG-Mod-Wikis/mods/baseitems/table-saw/
# https://tyson89.github.io/RaG-Mod-Wikis/mods/baseitems/workbench/
SUPPLY_SPAWNS = {
    BASE_ITEMS: ("rag_baseitems_sawblade", "rag_baseitems_control_unit", "rag_baseitems_electric_wires"),
}
SLUGS = {
    HUNTING_CABIN: "rag_hunting_cabin",
    BASE_BUILDING: "rag_basebuilding",
    BASE_ITEMS: "rag_baseitems",
    BEEHIVE: "rag_beehive",
    SOLITUDE: "solitude_storage",
    FORTIFICATIONS: "building_fortifications",
}
PUBLISHER_ECONOMY_DIRS = frozenset({
    "ce", "config", "configs", "db", "economy", "extra", "extras", "info", "serverconfig",
    "serverfiles", "serverxml", "types", "xml", "xmlfiles",
})


def _kit_xml(names: tuple[str, ...], supplies: tuple[str, ...] = ()) -> bytes:
    types = ElementTree.Element("types")
    for name in (*names, *supplies):
        entry = ElementTree.SubElement(types, "type", name=name)
        supply = name in supplies
        for key, value in (
            ("nominal", "8" if supply else "2"), ("lifetime", "14400" if supply else "28800"),
            ("restock", "600" if supply else "1800"), ("min", "5" if supply else "1"),
            ("quantmin", "-1"), ("quantmax", "-1"), ("cost", "100"),
        ):
            ElementTree.SubElement(entry, key).text = value
        ElementTree.SubElement(entry, "flags", count_in_cargo="0", count_in_hoarder="0",
                               count_in_map="1", count_in_player="0", crafted="0", deloot="0")
        ElementTree.SubElement(entry, "category", name="tools")
        for usage in ("Industrial", "Farm"):
            ElementTree.SubElement(entry, "usage", name=usage)
    ElementTree.indent(types)
    return ElementTree.tostring(types, encoding="utf-8", xml_declaration=True) + b"\n"


def _ce_candidates(root: Path, max_depth: int = 3):
    """Look only inside conventional Workshop server-data directories."""
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            continue
        normalized = re.sub(r"[\W_]", "", path.stem.casefold())
        if path.is_dir() and max_depth and (normalized in PUBLISHER_ECONOMY_DIRS or normalized == "avalon"):
            yield from _ce_candidates(path, max_depth - 1)
        elif path.is_file() and path.suffix.casefold() == ".xml" and "types" in normalized:
            if not any(excluded in normalized for excluded in ("spawnable", "randompreset", "event")):
                yield path


def _publisher_types(root: Path) -> bytes | None:
    candidates = []
    for path in _ce_candidates(root):
        try:
            with path.open("rb") as stream:
                data = stream.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                continue
            xml = data.decode("utf-8-sig")
            if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
                continue
            content = ElementTree.fromstring(xml)
        except (OSError, UnicodeError, ElementTree.ParseError):
            continue
        entries = content.findall("type")
        names = {entry.get("name", "").strip().casefold() for entry in entries}
        if content.tag != "types" or not entries or "" in names or len(names) != len(entries):
            continue
        # Only prefer an Avalon-specific file when several map variants exist.
        location = path.relative_to(root).as_posix().casefold()
        candidates.append(("avalon" not in location, data))
    if not candidates:
        return None
    rank = min(candidate[0] for candidate in candidates)
    unique = {data for priority, data in candidates if priority == rank}
    if len(unique) == 1:
        return unique.pop()
    return None


def _prepare_cabin(profiles: Path) -> None:
    directory = profiles
    directory.mkdir(parents=True, exist_ok=True)
    source_type(directory)
    for name in CABIN_SETTINGS.parts[:-1]:
        directory /= name
        directory.mkdir(exist_ok=True)
        source_type(directory)
    config = directory / CABIN_SETTINGS.name
    if config.exists() or config.is_symlink():
        source_type(config)
        if not config.is_file():
            raise ValueError("RaG Hunting Cabin settings must be a JSON file.")
        return
    atomic_text(config, json.dumps(CABIN_CONFIG, indent=2) + "\n")


def _beehive_role(name: str, properties: dict) -> str | None:
    """Identify a role only after the installed config declared the class."""
    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    if (not normalized.startswith("rag") or normalized.endswith("base")
            or any(word in normalized for word in ("ruined", "destroyed"))):
        return None
    label = properties.get("displayName", "")
    label = re.sub(r"[^a-z0-9]", "", label.casefold()) if isinstance(label, str) else ""
    if "spatula" in normalized or "honeycombspatula" in label:
        return "spatula"
    if "dipper" in normalized or "honeycombdipper" in label:
        return "dipper"
    # Exclude explicitly filled/product variants. A generic pot is spawned at
    # zero quantity; its exact class identity still comes from the mod config.
    if ("honeypot" in normalized or label in {"honeypot", "emptyhoneypot"}) and not any(
            word in normalized + label for word in ("filled", "full")):
        return "honeypot"
    return None


def _beehive_config_classes(workshop: Path) -> tuple[dict[str, tuple[str, dict]], tuple[str, ...]]:
    """Inspect bounded installed config payloads, without extracting assets."""
    from azimuth_app.checks.vehicle_wrap_evidence import _directory
    configs = []
    notices = []
    directories = [workshop]
    directories.extend(path for path in workshop.iterdir()
                       if path.name.casefold() == "addons" and stat.S_ISDIR(source_type(path)))
    budget = 16 * 1024 * 1024
    inspected = 0
    for directory in directories:
        for path in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.casefold() != ".pbo" and path.name.casefold() not in {"config.cpp", "config.bin"}:
                continue
            if not stat.S_ISREG(source_type(path)):
                raise ValueError("BeeHive class evidence must be an ordinary file.")
            inspected += 1
            if inspected > 32:
                raise ValueError("Too many BeeHive config archives to inspect safely.")
            with path.open("rb") as stream:
                if path.suffix.casefold() != ".pbo":
                    data = stream.read(min(budget, 4 * 1024 * 1024) + 1)
                    if len(data) > min(budget, 4 * 1024 * 1024):
                        raise ValueError("BeeHive config exceeds the evidence limit.")
                    budget -= len(data)
                    configs.append((path.name, data))
                    continue
                members, _, _, _ = _directory(stream, path.stat().st_size, 8 * 1024 * 1024)
                for member in members:
                    if Path(member.name).name.casefold() not in {"config.cpp", "config.bin"}:
                        continue
                    if member.method != 0:
                        notices.append(f"BeeHive class evidence {path.name}/{member.name} is compressed; publisher types remain authoritative.")
                        continue
                    if member.stored_size > min(budget, 4 * 1024 * 1024):
                        raise ValueError("BeeHive config exceeds the evidence limit.")
                    stream.seek(member.offset)
                    data = stream.read(member.stored_size)
                    if len(data) != member.stored_size:
                        raise ValueError("BeeHive config changed during inspection.")
                    budget -= len(data)
                    configs.append((path.name + "/" + member.name, data))
    declared = {}
    conflicting = set()
    for label, data in configs:
        try:
            parsed = decode(data)
            if parsed.get("preprocessor_directives"):
                notices.append(f"BeeHive class evidence {label} uses preprocessing; no class names were inferred from it.")
                continue
            classes = parsed["root"]["classes"].get("CfgVehicles", {}).get("classes", {})
            for name, node in classes.items():
                key = name.casefold()
                if key in declared:
                    previous = declared[key][1]
                    if (previous.get("parent", "").casefold(), previous["properties"]) != (
                            node.get("parent", "").casefold(), node["properties"]):
                        conflicting.add(key)
                else:
                    declared[key] = (name, node)
        except (DecodeError, UnicodeError, KeyError, TypeError, RecursionError) as error:
            notices.append(f"Could not verify BeeHive class evidence {label}: {error}")
    candidates = {}
    ambiguous = set()
    for key, (name, node) in declared.items():
        try:
            lineage, visited = [], set()
            current = node
            while current is not None:
                current_key = current["name"].casefold()
                if current_key in visited or len(visited) >= 64 or current_key in conflicting:
                    raise DecodeError("conflicting, cyclic or overly deep local inheritance")
                visited.add(current_key)
                lineage.append(current["properties"])
                parent = declared.get(current.get("parent", "").casefold())
                current = parent[1] if parent else None
            props = {}
            for attributes in reversed(lineage):
                props.update(attributes)
            if props.get("scope") != 2:
                continue
            role = _beehive_role(name, props)
            if role is None:
                continue
            candidate = (name, props)
            if role in candidates and candidates[role] != candidate:
                ambiguous.add(role)
            else:
                candidates[role] = candidate
        except (DecodeError, KeyError, TypeError) as error:
            notices.append(f"Could not verify BeeHive class {name}: {error}")
    for role in ambiguous:
        candidates.pop(role, None)
        notices.append(f"BeeHive {role} has several concrete class candidates; no arbitrary class was selected.")
    return candidates, tuple(notices)


def _companion_role_annotation(entry: ElementTree.Element) -> str | None:
    for child in entry:
        if child.tag is ElementTree.Comment and (child.text or "").startswith("Azimuth companion role: "):
            role = child.text.partition(": ")[2].strip()
            if role in {"spatula", "dipper", "honeypot"}:
                return role
    return None


def _beehive_types(mission: Path, workshop: Path) -> tuple[bytes, tuple[str, ...]]:
    """Keep the publisher's companion items instead of replacing them with one kit.

    Class identities come only from installed publisher XML or already installed
    mission rows. In particular, never invent a classname from a display name.
    Authored zero/crafted/event rows are retained, not converted into natural loot.
    """
    data = _publisher_types(workshop)
    retained = mission / "azimuth_workshop" / SLUGS[BEEHIVE] / "types.xml"
    if data is None and retained.is_file():
        source_type(retained)
        with retained.open("rb") as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
    root = ElementTree.Element("types")
    names = set()
    verified_extras, extra_notices = verified_rows(BEEHIVE, workshop)
    verified_extra_classes = {key for key in verified_extras
                              if key.startswith(('beekeeper_', 'honey_glass_'))}
    if data is not None:
        if len(data) > 2 * 1024 * 1024 or any(marker in data.upper() for marker in (b"<!DOCTYPE", b"<!ENTITY", b"\0")):
            raise ValueError("Unsupported RaG BeeHive economy XML.")
        try:
            publisher = ElementTree.fromstring(data, parser=ElementTree.XMLParser(
                target=ElementTree.TreeBuilder(insert_comments=True)))
        except ElementTree.ParseError as error:
            raise ValueError(f"Invalid RaG BeeHive economy XML: {error}") from error
        if publisher.tag != "types":
            raise ValueError("RaG BeeHive economy XML must contain <types>.")
        for entry in publisher.findall("type"):
            name = entry.get("name", "")
            normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
            # Some releases bundle full-map examples. Import this mod's rows
            # only; another map's vanilla economy must not leak into Avalon.
            verified_extra = name.casefold() in verified_extra_classes
            if (not normalized.startswith("rag") and not verified_extra) or (not verified_extra
                    and not _companion_role_annotation(entry) and not any(
                    term in normalized for term in ("bee", "honey", "testtube", "spatula", "dipper"))):
                continue
            if normalized.startswith(("ragbaseitems", "ragbb", "raghuntingcabin")):
                continue
            if name.casefold() == "rag_beehive_kit":
                continue
            if name.casefold() in names:
                raise ValueError(f"Duplicate RaG BeeHive economy class: {name}.")
            names.add(name.casefold())
            root.append(entry)
    root.extend(ElementTree.fromstring(_kit_xml(KIT_SPAWNS[BEEHIVE])))

    # Missing publisher rows can be recovered from concrete installed classes.
    # Existing rows, including deliberate crafted/zero rows, keep their policy.
    discovered, evidence_notices = {}, ()
    if _beehive_notices(root):
        try:
            discovered, evidence_notices = _beehive_config_classes(workshop)
        except (OSError, ValueError) as error:
            evidence_notices = (f"BeeHive installed class evidence could not be read: {error}",)
    registered = {entry.get("name", "").casefold() for entry in root.findall("type")}
    for role, (name, properties) in discovered.items():
        if name.casefold() in registered:
            continue
        node = ElementTree.fromstring(_kit_xml((), (name,))).find("type")
        # An XML comment preserves the verified role for names identified by
        # displayName; it adds no unrecognized CE element or gameplay setting.
        node.append(ElementTree.Comment("Azimuth companion role: " + role))
        if role == "honeypot":
            # A supply pot should start empty, regardless of the class default.
            node.find("quantmin").text = "0"
            node.find("quantmax").text = "0"
        root.append(node)
        registered.add(name.casefold())
    # All original publisher types keep their authored numbers and restrictions.
    # If that XML changes after this snapshot, keep any previously registered
    # verified extra classes instead of silently deleting them on restart.
    if not verified_extra_classes:
        for key, entry in remembered_rows(mission, SLUGS[BEEHIVE], evidence_id=BEEHIVE).items():
            if key.startswith(('beekeeper_', 'honey_glass_')) and key not in registered:
                root.append(entry)
                registered.add(key)
    notices = _beehive_notices(root) + evidence_notices + extra_notices
    ElementTree.indent(root)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n", notices


def _beehive_notices(root: ElementTree.Element) -> tuple[str, ...]:
    natural = []
    verified_roles = set()
    for entry in root.findall("type"):
        flags = entry.find("flags")
        if (int(entry.findtext("nominal", "0")) > 0
                and (flags is None or (flags.get("crafted", "0") == "0" and flags.get("deloot", "0") == "0"))):
            natural.append(re.sub(r"[^a-z0-9]", "", entry.get("name", "").casefold()))
            if role := _companion_role_annotation(entry):
                verified_roles.add(role)
    required = (("Honeycomb Spatula", "spatula"), ("Honeycomb Dipper", "dipper"), ("empty Honey Pot", "honeypot"))
    missing = [label for label, token in required
               if token not in verified_roles and not any(token in name for name in natural)]
    notices = ()
    if missing:
        notices = ("RaG BeeHive kit is available, but honey production is not fully configured. "
                   "Publisher companion-item loot is still needed for: " + ", ".join(missing) +
                   ". Azimuth imports the installed mod's types XML when supplied; it does not guess class names.",)
    return notices


def beehive_setup_notices(mission: Path) -> tuple[str, ...]:
    """Read-only check of the registered BeeHive companion-item rows."""
    path = mission / "azimuth_workshop" / SLUGS[BEEHIVE] / "types.xml"
    if not path.exists():
        return ("RaG BeeHive economy is not registered for this mission.",)
    source_type(path)
    with path.open("rb") as stream:
        data = stream.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024 or any(marker in data.upper() for marker in (b"<!DOCTYPE", b"<!ENTITY", b"\0")):
        raise ValueError("Unsupported RaG BeeHive economy XML.")
    root = ElementTree.fromstring(data, parser=ElementTree.XMLParser(
        target=ElementTree.TreeBuilder(insert_comments=True)))
    if root.tag != "types":
        raise ValueError("RaG BeeHive economy XML must contain <types>.")
    return _beehive_notices(root)


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    """Register known loot kits without replacing Avalon or Hunter Mods XML."""
    notices = []
    for item, names in KIT_SPAWNS.items():
        if item in mods:
            slug = SLUGS[item]
            if item == BEEHIVE:
                data, missing = _beehive_types(mission, mods[item])
                notices.extend(missing)
            elif item == BASE_ITEMS:
                # Add only audited concrete publisher rows absent from existing
                # Avalon layers. The curated kits and repair supplies win.
                root = ElementTree.fromstring(_kit_xml(names, SUPPLY_SPAWNS.get(item, ())))
                imported, messages = verified_rows(BASE_ITEMS, mods[item])
                notices.extend(messages)
                own_layer = 'azimuth_workshop/' + slug
                economy = read_economy(mission, exclude_folders=(own_layer, 'azimuth_loot_balance', 'azimuth_economy_repairs'))
                if economy.warnings:
                    notices.append('RaG BaseItems publisher loot not imported: current mission economy is ambiguous.')
                    imported = {}
                placed = load_sites(mission)
                existing = set(economy.types) | {node.get('name', '').casefold() for node in root.findall('type')}
                approved = remembered_rows(mission, slug, evidence_id=BASE_ITEMS)
                for key, entry in (imported or approved).items():
                    if key in existing:
                        continue
                    if ordinary_target(entry) and not supported_on_map(entry, placed):
                        adjusted = correct_two_publisher_placement_gaps(entry, placed) if imported else None
                        if adjusted is None:
                            notices.append('RaG BaseItems ordinary loot needs a matching authored Avalon placement: ' + entry.get('name', ''))
                            continue
                        entry = adjusted
                        notices.append('RaG BaseItems: repaired missing Avalon loot placement filters for ' + entry.get('name', ''))
                    root.append(entry)
                    existing.add(key)
                ElementTree.indent(root)
                data = ElementTree.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
            else:
                data = _kit_xml(names, SUPPLY_SPAWNS.get(item, ()))
            register_bytes(mission, slug, {"types": (f"{slug}_types.xml", data)})
    if HUNTING_CABIN in mods:
        _prepare_cabin(profiles)

    for item, label in ((SOLITUDE, "Solitude"), (FORTIFICATIONS, "Building Fortifications")):
        if item not in mods:
            continue
        data = _publisher_types(mods[item])
        if data is not None:
            slug = SLUGS[item]
            register_bytes(mission, slug, {"types": (f"{slug}_types.xml", data)})
        elif item == SOLITUDE:
            retained = mission / "azimuth_workshop" / SLUGS[item] / "types.xml"
            if retained.is_file():
                source_type(retained)
                with retained.open("rb") as stream:
                    data = stream.read(2 * 1024 * 1024 + 1)
                register_bytes(mission, SLUGS[item], {"types": ("types.xml", data)})
            else:
                notices.append(f"{label} loaded. Natural furniture loot needs the publisher's types XML.")
    return tuple(notices)
