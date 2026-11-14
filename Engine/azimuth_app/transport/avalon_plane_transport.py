"""Register the installed author's An-2 data at an existing Avalon airfield site.

No aircraft assets, class definitions or example coordinates are bundled here.
The publisher's ServerFiles are the authority for item and attachment names.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
import stat
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_economy import register_bytes
from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text

WORKSHOP_ID = "3834034916"
ITEMS = ((WORKSHOP_ID, "An-2 Biplane"),)
SLUG = "an2_plane"
EVENT = "VehicleAzimuthAn2Plane"
SOURCE_EVENT = "StaticHeliCrash"
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_FILES = 256
SOURCE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id=3834034916"
REPORT = Path("Azimuth", "Transport Plane Audit.json")
# These are concrete scope=2 CarDoor classes in the installed author's an2mod
# config (SHA256 9ce446cd57295143a37ebfa3e2dce956551efb3c5e424f8ff0ac3518524397b8).
# Its supplied spawnabletypes attaches them, but types.xml intentionally only
# describes the aircraft and loose wheels. Doors need persistence, not invented
# industrial loot or removal from the author's assembly recipe.
PERSISTENT_DOORS = {
    "an2_door_cargo": "An2_Door_Cargo", "an2_door_pax": "An2_Door_Pax",
    "an2_door_storl": "An2_Door_StorL", "an2_door_storr": "An2_Door_StorR",
}


def _read(path: Path, root_name: str | None = None) -> ET.Element:
    if not stat.S_ISREG(source_type(path)):
        raise ValueError(f"Expected a regular XML file: {path.name}.")
    with path.open("rb") as stream:
        data = stream.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES or any(x in data.upper() for x in (b"<!DOCTYPE", b"<!ENTITY", b"\0")):
        raise ValueError(f"Unsupported XML: {path.name}.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # Publisher server snippets sometimes omit their outer document root.
        if root_name is None:
            raise ValueError(f"Invalid XML: {path.name}.") from None
        text = data.decode("utf-8-sig").strip()
        text = re.sub(r"^<\?xml[^?]*\?>", "", text).strip()
        try:
            root = ET.fromstring(f"<{root_name}>{text}</{root_name}>")
        except ET.ParseError:
            raise ValueError(f"Invalid publisher XML: {path.name}.") from None
    if root_name and root.tag != root_name:
        if root.tag == ("event" if root_name == "events" else "type"):
            outer = ET.Element(root_name)
            outer.append(root)
            root = outer
        else:
            raise ValueError(f"Expected <{root_name}> in {path.name}.")
    return root


def _xml(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _publisher_files(workshop: Path) -> dict[str, list[Path]]:
    if not stat.S_ISDIR(source_type(workshop)):
        raise ValueError("An-2 Workshop folder is unavailable.")
    roots = [p for p in workshop.iterdir() if p.name.casefold().replace("_", "").replace(" ", "") == "serverfiles"]
    if len(roots) != 1 or not stat.S_ISDIR(source_type(roots[0])):
        raise ValueError("An-2 Biplane needs its publisher ServerFiles folder. Verify the Steam Workshop download.")
    found = {kind: [] for kind in ("types", "spawnabletypes", "events")}
    pending = [(roots[0], 0)]
    scanned = 0
    while pending:
        folder, depth = pending.pop()
        for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            scanned += 1
            if scanned > MAX_FILES:
                raise ValueError("An-2 ServerFiles has too many entries to inspect safely.")
            mode = source_type(path)
            if stat.S_ISDIR(mode):
                if depth < 4:
                    pending.append((path, depth + 1))
                continue
            if not stat.S_ISREG(mode) or path.suffix.casefold() != ".xml":
                continue
            name = path.stem.casefold()
            kind = ("spawnabletypes" if "spawnable" in name else
                    "types" if "types" in name and "random" not in name else
                    "events" if "events" in name and "spawn" not in name and "group" not in name else None)
            if kind:
                found[kind].append(path)
    return found


def _publisher_data(workshop: Path):
    files = _publisher_files(workshop)
    entries = {kind: {} for kind in files}
    sources = {}
    for kind, paths in files.items():
        for path in paths:
            root = _read(path, kind)
            for node in root.findall("event" if kind == "events" else "type"):
                name = node.get("name", "").strip()
                if kind != "events" and not name.casefold().startswith("an2"):
                    continue
                if kind == "events" and not any(c.get("type", "").casefold().startswith("an2") for c in node.findall("children/child")):
                    continue
                if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
                    raise ValueError(f"Invalid An-2 publisher class/event in {path.name}.")
                key = name.casefold()
                # Whitespace-only differences between examples are harmless.
                canonical = ET.canonicalize(ET.tostring(node, encoding="unicode"), strip_text=True)
                prior = entries[kind].get(key)
                if prior is not None and prior[1] != canonical:
                    raise ValueError(f"An-2 publisher files contain conflicting {kind} definitions for {name}.")
                entries[kind][key] = (node, canonical)
                sources.setdefault(kind, set()).add(path.relative_to(workshop).as_posix())
    # The optional paint packs have suffixed class names. Select a literal base
    # aircraft present in all three publisher documents, never invent its name.
    children = {n.get("type", "").casefold() for event, _ in entries["events"].values()
                for n in event.findall("children/child")}
    candidates = [key for key in entries["types"] if key.endswith("biplane")
                  and key in entries["spawnabletypes"] and key in children]
    if len(candidates) != 1:
        raise ValueError("An-2 base aircraft is not uniquely confirmed in its publisher types, events and spawnabletypes XML. "
                         "Optional paint-pack aircraft are not guessed or enabled.")
    key = candidates[0]
    base = deepcopy(entries["types"][key][0])
    spawnable = deepcopy(entries["spawnabletypes"][key][0])
    types = ET.Element("types")
    types.append(base)
    required_parts = {n.get("name", "").casefold() for n in spawnable.findall("attachments/item")
                      if n.get("name", "").casefold().startswith("an2")}
    spare_parts = []
    for part in sorted(required_parts):
        if part not in entries["types"]:
            if part not in PERSISTENT_DOORS:
                raise ValueError(f"An-2 attachment {part} has no verified publisher persistence/loot definition.")
            node = ET.SubElement(types, "type", name=PERSISTENT_DOORS[part])
            for field, value in (("nominal", "0"), ("lifetime", "28800"), ("restock", "0"),
                                 ("min", "0"), ("quantmin", "-1"), ("quantmax", "-1"), ("cost", "100")):
                ET.SubElement(node, field).text = value
            ET.SubElement(node, "flags", count_in_cargo="0", count_in_hoarder="0", count_in_map="1",
                          count_in_player="0", crafted="0", deloot="0")
            continue
        node = deepcopy(entries["types"][part][0])
        flags = node.find("flags")
        if flags is None or flags.get("crafted", "0") != "0":
            raise ValueError(f"An-2 attachment {part} does not have a verified ordinary spare-part rule.")
        # Maintain the author's identity/lifetime; make required spare parts
        # obtainable through Avalon's ordinary industrial/farm tool routes.
        for field, value in (("nominal", "6"), ("min", "3"), ("restock", "600")):
            element = node.find(field)
            if element is None:
                element = ET.SubElement(node, field)
            element.text = value
        flags.attrib.update(count_in_map="1", count_in_player="0", count_in_cargo="0", count_in_hoarder="0", deloot="0")
        for kind in ("category", "usage", "value", "tag"):
            for old in list(node.findall(kind)):
                node.remove(old)
        ET.SubElement(node, "category", name="tools")
        for usage in ("Industrial", "Farm"):
            ET.SubElement(node, "usage", name=usage)
        types.append(node)
        spare_parts.append(part)
    spawnables = ET.Element("spawnabletypes")
    spawnables.append(spawnable)
    # Do not carry optional cargo, paint packs or another map's population rule.
    events = ET.Element("events")
    event = ET.SubElement(events, "event", name=EVENT)
    for field, value in (("nominal", "1"), ("min", "1"), ("max", "1"), ("lifetime", "3888000"),
                         ("restock", "600"), ("saferadius", "100"), ("distanceradius", "100"), ("cleanupradius", "200")):
        ET.SubElement(event, field).text = value
    ET.SubElement(event, "flags", deletable="0", init_random="0", remove_damaged="1")
    for field, value in (("position", "fixed"), ("limit", "mixed"), ("active", "1")):
        ET.SubElement(event, field).text = value
    ET.SubElement(ET.SubElement(event, "children"), "child", type=base.get("name"), min="1", max="1", lootmin="0", lootmax="0")
    return base.get("name"), {"types": types, "spawnabletypes": spawnables, "events": events}, {
        kind: sorted(paths) for kind, paths in sources.items()}, spare_parts


def _position(root: ET.Element, groups: ET.Element):
    objects = []
    hangars = []
    for group in groups.findall("group"):
        try:
            x, y, z = map(float, group.get("pos", "").split())
        except ValueError:
            continue
        if not all(math.isfinite(v) for v in (x, y, z)):
            continue
        objects.append((x, z))
        if group.get("name", "").casefold().startswith("land_hangar_avalon"):
            hangars.append((x, z))
    if not objects or not hangars:
        raise ValueError("No authored Avalon airfield hangar anchor is available for the An-2.")
    existing = root.findall(f"event[@name='{EVENT}']")
    if len(existing) > 1:
        raise ValueError("Duplicate managed An-2 spawn-location events.")
    source = root.findall(f"event[@name='{SOURCE_EVENT}']")
    if len(source) != 1:
        raise ValueError("The source helicopter-crash location list is not unique.")
    pools = [(existing[0], p) for p in existing[0].findall("pos")] if existing else [(source[0], p) for p in source[0].findall("pos")]
    candidates = []
    for owner, pos in pools:
        try:
            x, z, a = (float(pos.get(k, "nan")) for k in ("x", "z", "a"))
        except ValueError:
            continue
        if not all(math.isfinite(v) for v in (x, z, a)) or x <= 0 or z <= 0:
            continue
        anchor = min(math.hypot(x - xx, z - zz) for xx, zz in hangars)
        clearance = min(math.hypot(x - xx, z - zz) for xx, zz in objects)
        if anchor <= 250 and clearance >= 60:
            candidates.append((anchor, x, z, owner, pos, clearance))
    if not candidates:
        raise ValueError("No existing airfield event position passes the An-2 static location checks.")
    _, _, _, owner, position, clearance = min(candidates, key=lambda n: n[:3])
    result = deepcopy(position)
    if existing:
        if len(existing[0].findall("pos")) != 1:
            raise ValueError("The managed An-2 spawn event contains unexpected additional positions.")
    else:
        owner.remove(position)
        event = ET.SubElement(root, "event", name=EVENT)
        event.append(result)
    return result.attrib.copy(), round(clearance, 3)


def _record(profiles: Path, report: dict) -> None:
    folder = profiles / REPORT.parent
    folder.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(source_type(folder)):
        raise OSError("Expected an ordinary transport audit folder.")
    target = profiles / REPORT
    if target.exists() or target.is_symlink():
        if not stat.S_ISREG(source_type(target)):
            raise OSError("Expected a regular transport audit file.")
    payload = json.dumps(report, indent=2) + "\n"
    if not target.is_file() or target.read_bytes() != payload.encode("utf-8"):
        atomic_text(target, payload)


def _needs_setup(profiles: Path, error: Exception, *, preserved: bool = False) -> tuple[str, ...]:
    message = f"NEEDS SETUP: An-2 Biplane. Airplane is NOT configured: {error} "
    message += "Its previous mission data was preserved." if preserved else "Other transport categories can still run."
    try:
        _record(profiles, {"status": "NEEDS_SETUP", "workshop_id": WORKSHOP_ID,
                          "source": SOURCE_URL, "message": message,
                          "native_validation": "NOT_RUN", "mission_data_changed": False})
    except OSError:
        message += " The transport diagnostic could not be written."
    return (message,)


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    if WORKSHOP_ID not in mods:
        return ()
    mission = mission.resolve()
    try:
        classname, roots, sources, parts = _publisher_data(mods[WORKSHOP_ID])
        positions_path = mission / "cfgeventspawns.xml"
        positions = _read(positions_path)
        if positions.tag != "eventposdef":
            raise ValueError("Expected Avalon eventposdef spawn coordinates.")
        point, clearance = _position(positions, _read(mission / "mapgrouppos.xml"))
        from azimuth_app.economy.avalon_loot_sites import load_sites
        sites = load_sites(mission)
        if parts and (sites is None or not sites.supports(category="tools", usages=("Industrial", "Farm"))):
            raise ValueError("No verified ordinary tool route exists for the An-2 spare parts.")
    except (OSError, ValueError, ET.ParseError, UnicodeError) as error:
        return _needs_setup(profiles, error)
    folder = mission / "azimuth_workshop" / SLUG
    targets = [mission / "cfgeconomycore.xml", positions_path, *(folder / (kind + ".xml") for kind in roots)]
    before = {path: path.read_bytes() if path.is_file() else None for path in targets}
    try:
        register_bytes(mission, SLUG, {kind: (kind + ".xml", _xml(root)) for kind, root in roots.items()})
        text = _xml(positions).decode("utf-8")
        if positions_path.read_text(encoding="utf-8-sig") != text:
            atomic_text(positions_path, text)
    except (OSError, ValueError) as error:
        for path, original in before.items():
            if original is None:
                path.unlink(missing_ok=True)
            elif not path.is_file() or path.read_bytes() != original:
                atomic_text(path, original.decode("utf-8-sig"))
        return _needs_setup(profiles, error, preserved=True)
    report = {
        "status": "CONFIGURED_FROM_INSTALLED_PUBLISHER_XML", "workshop_id": WORKSHOP_ID,
        "source": SOURCE_URL, "vehicle_class": classname, "publisher_files": sources,
        "ordinary_spare_parts": parts, "mapwide_target": 1, "position": point,
        "position_source": "Existing Avalon StaticHeliCrash airfield point, transferred to avoid two event types sharing it",
        "nearest_mapped_object_origin_metres": clearance,
        "native_validation": "NOT_RUN: terrain slope, unlisted objects, wingspan clearance, takeoff/landing and physics need DayZ",
        "optional_paint_packs": "Not enabled; their independent Workshop packs are not loaded",
    }
    _record(profiles, report)
    return (f"An-2 airplane configured from installed publisher XML: {classname}, target 1 at the Avalon airfield. "
            "Native clearance and flight still need an in-game check.",)
