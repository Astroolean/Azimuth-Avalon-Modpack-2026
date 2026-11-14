"""Prepare signed Workshop mods for Avalon's optional immersion preset."""
from __future__ import annotations

from copy import deepcopy
import math
import os
from pathlib import Path
import stat
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET
from xml.parsers import expat

from azimuth_app.economy.avalon_economy import MAX_XML_BYTES, ROOTS, register_bytes
from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text, children

SURVIVOR = "2918418331"
BICYCLE = "2971190303"
DOG = "2471347750"
GUITAR = "3821819263"
HORSE = "3295021220"

ITEMS = (
    (SURVIVOR, "Survivor Animations"),
    (BICYCLE, "DayZ-Bicycle"),
    (DOG, "DayZ-Dog"),
    (GUITAR, "DayZ-Guitar"),
)
EXTRA_ITEMS = ((HORSE, "DayZ Horse"),)

BIKE_POSITION_LIMIT = 40
DOG_KIT = "dog_shed_small_kit"
XML_FRAGMENT = expat.errors.codes[expat.errors.XML_ERROR_JUNK_AFTER_DOC_ELEMENT]


def _bytes(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _supported_xml(data: bytes, path: Path) -> None:
    if len(data) > MAX_XML_BYTES or any(mark in data.upper() for mark in (b"<!DOCTYPE", b"<!ENTITY", b"\0")):
        raise ValueError(f"Unsupported XML in {path}.")


def _xml(data: bytes, path: Path, snippet_root: str = "") -> ET.Element:
    _supported_xml(data, path)
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        return ET.fromstring(data, parser=parser)
    except ET.ParseError as error:
        if snippet_root and error.code == XML_FRAGMENT:
            body = data.decode("utf-8-sig").lstrip()
            if body.startswith("<?xml"):
                body = body.partition("?>")[2]
            return _xml(f"<{snippet_root}>{body}</{snippet_root}>".encode(), path)
        raise ValueError(f"Invalid XML in {path}: {error}") from error


def _ce_examples(folder: Path, prefix: str, kinds: tuple[str, ...]) -> dict[str, ET.Element]:
    """Find item-specific examples without importing another map's mission."""
    if not stat.S_ISDIR(source_type(folder)):
        raise ValueError(f"The Workshop folder for {prefix} is unavailable.")
    matches: dict[str, dict[bytes, ET.Element]] = {kind: {} for kind in kinds}
    for current, folders, files in os.walk(folder, followlinks=False):
        base = Path(current)
        depth = len(base.relative_to(folder).parts)
        folders[:] = [
            name for name in sorted(folders, key=str.casefold)
            if depth < 4 and not name.casefold().startswith(("dayzoffline.", "empty."))
            and name.casefold() not in {"addons", "keys", "mpmissions", "storage_1"}
            and not (base / name).is_symlink()
            and not getattr((base / name).stat(), "st_file_attributes", 0) & 0x400
        ]
        for name in sorted(files, key=str.casefold):
            path = base / name
            if not name.casefold().endswith(".xml") or path.is_symlink():
                continue
            hint = next((kind for kind in ("spawnabletypes", "events", "types")
                         if kind in name.casefold()), None)
            if hint not in kinds:
                continue
            if not stat.S_ISREG(source_type(path)):
                continue
            with path.open("rb") as stream:
                data = stream.read(MAX_XML_BYTES + 1)
            root = _xml(data, path, snippet_root=hint)
            if root.tag in {"type", "event"}:
                entry, root = root, ET.Element(hint)
                root.append(entry)
            if root.tag in kinds:
                # Normalization makes identical examples in different folders harmless.
                selected = _selected(root, prefix)
                if len(selected):
                    matches[root.tag][_bytes(selected)] = selected

    examples = {}
    for kind, entries in matches.items():
        if len(entries) > 1:
            raise ValueError(f"{folder.name} contains several different {kind} examples.")
        if entries:
            examples[kind] = next(iter(entries.values()))
    if "types" not in examples:
        raise ValueError(f"{folder.name} is missing its author-provided types XML example.")
    return examples


def _bike_class(name: str) -> bool:
    return any(part in name.casefold() for part in ("bicycle", "old_bike"))


def _item_entry(entry: ET.Element, prefix: str) -> bool:
    """Recognize the selected mod's names even inside a full economy example."""
    name = entry.get("name", "").casefold()
    if prefix == "bike":
        if entry.tag == "event":
            return any(_bike_class(child.get("type", ""))
                       for child in entry.findall("./children/child"))
        return _bike_class(name)
    if prefix == "dog":
        return name.startswith(("dog_", "doghouse", "dayzdog"))
    return any(part in name for part in ("guitar", "sheetmusic", "sheet_music", "musicnotes", "music_notes"))


def _selected(root: ET.Element, prefix: str) -> ET.Element:
    selected = ET.Element(root.tag, root.attrib)
    for entry in root:
        if entry.tag != ("event" if root.tag == "events" else "type") or not _item_entry(entry, prefix):
            continue
        copied = deepcopy(entry)
        if copied.tag == "event":
            variants = copied.find("children")
            for variant in variants.findall("child"):
                if not _bike_class(variant.get("type", "")):
                    variants.remove(variant)
        selected.append(copied)
    return selected


def _bike_events(examples: dict[str, ET.Element]) -> ET.Element:
    if "events" in examples:
        events = _selected(examples["events"], "bike")
    else:
        types = {entry.get("name", "").casefold(): entry.get("name") for entry in examples["types"].findall("type")}
        if "old_bike" not in types:
            raise ValueError("The DayZ-Bicycle example lacks a bicycle event and the Old_Bike class.")
        events = ET.Element("events")
        event = ET.SubElement(events, "event", name="VehicleAzimuthBicycle")
        for key, value in {
            "nominal": "3", "min": "1", "max": "3", "lifetime": "300", "restock": "0",
            "saferadius": "500", "distanceradius": "500", "cleanupradius": "200",
        }.items():
            ET.SubElement(event, key).text = value
        ET.SubElement(event, "flags", deletable="0", init_random="0", remove_damaged="1")
        for key, value in (("position", "fixed"), ("limit", "mixed"), ("active", "1")):
            ET.SubElement(event, key).text = value
        ET.SubElement(ET.SubElement(event, "children"), "child", type=types["old_bike"],
                      lootmax="0", lootmin="0", max="3", min="1")
    return events


def _bike_spawnables(examples: dict[str, ET.Element], retained: Path | None = None) -> ET.Element:
    variants = {child.get("type", "") for child in examples["events"].findall("./event/children/child")}
    types = {entry.get("name", "").casefold(): entry.get("name") for entry in examples["types"].findall("type")}
    if not variants or any(name.casefold() not in types for name in variants):
        raise ValueError("The DayZ-Bicycle event refers to a class missing from its types example.")
    spawnables = examples.get("spawnabletypes")
    if spawnables is None and retained is not None and retained.is_file():
        source_type(retained)
        with retained.open("rb") as stream:
            spawnables = _selected(_xml(stream.read(MAX_XML_BYTES + 1), retained), "bike")
        if spawnables.tag != "spawnabletypes":
            raise ValueError("Retained bicycle attachment XML must contain <spawnabletypes>.")
    if spawnables is not None:
        names = [entry.get("name", "").casefold() for entry in spawnables.findall("type")]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate retained/publisher bicycle attachment parent.")
        equipped = set()
        for entry in spawnables.findall("type"):
            wheels = 0
            for group in entry.findall("attachments"):
                try:
                    positive = 0 < float(group.get("chance", "1")) <= 1
                    present = any(0 < float(item.get("chance", "1")) <= 1
                                  and item.get("name", "").casefold() in types
                                  and item.get("name", "").casefold() in {"bicycle_wheel", "ladybicycle_wheel"}
                                  for item in group.findall("item"))
                except ValueError:
                    positive = present = False
                wheels += bool(positive and present)
            if wheels >= 2:
                equipped.add(entry.get("name", "").casefold())
        if any(name.casefold() not in equipped for name in variants):
            raise ValueError("The DayZ-Bicycle release lacks complete positive wheel rules for its event variants.")
        selected = ET.Element("spawnabletypes")
        selected.extend(deepcopy(entry) for entry in spawnables.findall("type")
                        if entry.get("name", "").casefold() in {name.casefold() for name in variants})
        return selected
    if not {"old_bike", "bicycle_wheel"} <= types.keys():
        raise ValueError("The DayZ-Bicycle release lacks attachment XML or the verified bicycle and wheel classes.")
    spawnables = ET.Element("spawnabletypes")
    for name in sorted(variants):
        if not name.casefold().startswith("old_bike"):
            raise ValueError(f"The DayZ-Bicycle release lacks wheel attachment rules for {name}.")
        entry = ET.SubElement(spawnables, "type", name=name)
        for _ in range(2):
            attachments = ET.SubElement(entry, "attachments", chance="1.00")
            ET.SubElement(attachments, "item", name=types["bicycle_wheel"], chance="1.00")
    return spawnables


def _position_key(position: ET.Element) -> tuple[float, float] | None:
    try:
        x, z = float(position.get("x", "")), float(position.get("z", ""))
        if not all(math.isfinite(value) and value > 0 for value in (x, z)):
            return None
        if not math.isfinite(float(position.get("a", "0"))):
            return None
    except ValueError:
        return None
    return x, z


def _bike_positions(mission: Path, events: ET.Element) -> tuple[Path, bytes] | None:
    path = children(mission).get("cfgeventspawns.xml", mission / "cfgeventspawns.xml")
    if not path.is_file():
        raise ValueError("Avalon's mission lacks cfgeventspawns.xml. Bike placement needs Avalon vehicle sites.")
    source_type(path)
    with path.open("rb") as stream:
        root = _xml(stream.read(MAX_XML_BYTES + 1), path)
    if root.tag != "eventposdef":
        raise ValueError("Expected <eventposdef> in Avalon's cfgeventspawns.xml.")
    original_events = {event.get("name", "").casefold(): event for event in root.findall("event")}
    bike_names = {event.get("name", "") for event in events.findall("event")
                  if (event.findtext("position") or "").strip().casefold() == "fixed"}
    anchors = {}
    # The supplied disabled sedan/truck lists contain legacy coordinates from
    # another terrain. Use Avalon's already active ADA/Sarka, Humvee and RZR
    # pools only; the old five-point generated list included remote bad sites.
    trusted_names = {"vehicleoffroadhatchback", "vehicleoffroad02", "vehiclesedan02"}
    for name, event in original_events.items():
        if name not in trusted_names:
            continue
        for point in event.findall("pos"):
            key = _position_key(point)
            if key is not None:
                anchors.setdefault(key, point)
    ordered = sorted(anchors)
    changed = False
    for name in sorted(bike_names):
        if not name:
            raise ValueError("A DayZ-Bicycle event has no name.")
        if not ordered:
            raise ValueError("No active Avalon land-vehicle sites were found for bicycle placement.")
        event = original_events.get(name.casefold())
        if event is None:
            event = ET.SubElement(root, "event", name=name)
            changed = True
        authored = next(row for row in events.findall("event") if row.get("name") == name)
        desired = max(5, min(BIKE_POSITION_LIMIT, int(authored.findtext("nominal", "3")) * 2))
        count = min(len(ordered), desired)
        points = [deepcopy(anchors[ordered[i * len(ordered) // count]]) for i in range(count)]
        if [p.attrib for p in event.findall("pos")] != [p.attrib for p in points]:
            for point in list(event.findall("pos")):
                event.remove(point)
            event.extend(points)
            changed = True
    if changed:
        return path, _bytes(root)
    return None


def _dog_types(folder: Path) -> ET.Element:
    dog_types = _ce_examples(folder, "dog", ("types",))["types"]
    kennel = next((entry for entry in dog_types.findall("type")
                   if entry.get("name", "").casefold() == DOG_KIT), None)
    if kennel is None:
        raise ValueError("The DayZ-Dog economy example lacks a deployable small kennel kit.")
    # Some publisher snippets define the class for lifetime or trader use.
    # Complete only the deployable kit's row for natural loot, keeping the
    # installed release's class identity and other Dog entries.
    for tag, default in (("nominal", 6), ("lifetime", 14400), ("restock", 0),
                         ("min", 3), ("quantmin", -1), ("quantmax", -1), ("cost", 100)):
        node = kennel.find(tag)
        if node is None:
            node = ET.SubElement(kennel, tag)
            node.text = str(default)
        elif tag in {"nominal", "min"}:
            node.text = str(max(int(node.text or "0"), default))
    nominal = kennel.find("nominal")
    nominal.text = str(max(int(nominal.text), int(kennel.findtext("min"))))
    flags = kennel.find("flags")
    if flags is None:
        flags = ET.SubElement(kennel, "flags")
    for flag in ("count_in_cargo", "count_in_hoarder", "count_in_player"):
        flags.attrib.setdefault(flag, "0")
    flags.attrib.update(count_in_map="1", crafted="0", deloot="0")
    if not kennel.findall("category"):
        ET.SubElement(kennel, "category", name="tools")
    if not kennel.findall("usage"):
        for usage in ("Village", "Town", "Farm"):
            ET.SubElement(kennel, "usage", name=usage)
    return dog_types


def _register(mission: Path, slug: str, economy: dict[str, ET.Element]) -> None:
    register_bytes(mission, slug, {
        kind: (f"{slug}_{kind}.xml", _bytes(root)) for kind, root in economy.items()
    })


def _restore_positions(path: Path, original: bytes) -> None:
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(original)
            stream.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    """Stage publisher-supplied economy files and report any unavailable spawn rules."""
    messages = []
    try:
        bike = _ce_examples(mods[BICYCLE], "bike", tuple(ROOTS))
        bike["events"] = _bike_events(bike)
        bike["spawnabletypes"] = _bike_spawnables(
            bike, mission / "azimuth_workshop/dayz_bicycle/spawnabletypes.xml")
        positions = _bike_positions(mission, bike["events"])
    except (OSError, ValueError) as error:
        messages.append(f"DayZ-Bicycle loaded. Natural bicycle spawns need an Avalon setup. {error}")
    else:
        if positions is not None:
            path, data = positions
            original = path.read_bytes()
            atomic_text(path, data.decode("utf-8"))
        try:
            _register(mission, "dayz_bicycle", bike)
        except (OSError, ValueError):
            if positions is not None:
                _restore_positions(path, original)
            raise
        messages.append("Bicycles use up to forty locations from Avalon's active road-vehicle events.")

    try:
        dog_types = _dog_types(mods[DOG])
    except (OSError, ValueError) as error:
        messages.append(f"DayZ-Dog loaded. Natural kennel kit loot needs setup. {error}")
    else:
        _register(mission, "dayz_dog", {"types": dog_types})

    try:
        guitar = _ce_examples(mods[GUITAR], "guitar", ("types",))
        if not any("guitar" in entry.get("name", "").casefold()
                   and int(entry.findtext("nominal", "0")) > 0 for entry in guitar["types"]):
            raise ValueError("The publisher's guitar example does not enable guitar loot.")
    except (OSError, ValueError) as error:
        messages.append(f"DayZ-Guitar loaded. Follow its Workshop Readme.md for natural guitar loot. {error}")
    else:
        _register(mission, "dayz_guitar", {"types": guitar["types"]})
    # DayZ-Dog creates and owns the config under profiles/Dayz-Dog at first launch.
    return tuple(messages)
