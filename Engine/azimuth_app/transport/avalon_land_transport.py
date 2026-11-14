"""Enable Avalon road transport with native 1.30 motorcycles and spare parts.

Only managed CE layers, the required native motorcycle CE root registration,
and transport events' Avalon position lists are written. Publisher types,
event rules, spawnable definitions and saves stay put.
"""
from azimuth_app.paths import APP_ROOT
from copy import deepcopy
from pathlib import Path
import math
import stat
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_loot_audit import _effective, _load_layers, _references, _xml, read_economy
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import _read_xml, _write_core
from azimuth_app.core.worlds import atomic_text

ITEMS = ()
EXTRA_ITEMS = ()
FOLDER = "azimuth_transport_land"
EXCLUDED = {FOLDER, "azimuth_economy_repairs", "azimuth_loot_balance"}
SOURCE = APP_ROOT / "Custom/Transport/Vanilla130"
ROAD_EVENTS = ("VehicleCivilianSedan", "VehicleHatchback02", "VehicleTruck01")
ROAD_WHEELS = ("CivSedanWheel", "Hatchback_02_Wheel", "Truck_01_Wheel", "Truck_01_WheelDouble")
MOTORBIKES = (
    "Motorbike_01_Blue", "Motorbike_01_Red", "Motorbike_01_Yellow",
    "Motorbike_02_Blue", "Motorbike_02_Green", "Motorbike_02_Red", "Motorbike_02_Yellow",
)
MOTORBIKE_EVENT = "VehicleAzimuthMotorbike"
MOTORBIKE_TARGET = 14
POSITION_LIMIT = 28
EVENT_FIELDS = ("nominal", "min", "max", "lifetime", "restock", "saferadius",
                "distanceradius", "cleanupradius", "flags", "position", "limit",
                "active", "children")


def _set(node, name, value):
    child = node.find(name)
    if child is None:
        child = ET.SubElement(node, name)
    child.text = str(value)


def _bytes(root):
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _point_key(point):
    try:
        x, z = (float(point.get(key, "")) for key in ("x", "z"))
        if not all(math.isfinite(value) and value > 0 for value in (x, z)):
            return None
        for key in ("y", "a"):
            if key in point.attrib and not math.isfinite(float(point.get(key))):
                return None
        return x, z
    except ValueError:
        return None


def _positions(mission):
    path = mission / "cfgeventspawns.xml"
    root = _xml(mission, path, "eventposdef")
    named = {}
    for event in root.findall("event"):
        key = event.get("name", "").casefold()
        if key in named:
            raise ValueError(f"Duplicate vehicle position event: {event.get('name')}.")
        named[key] = event
    # The disabled sedan/hatchback/truck lists in the supplied mission contain
    # inherited coordinates that do not match Avalon settlements. Trust only
    # the map's already active ADA/Sarka, Humvee and RZR ground-vehicle pools.
    # Source coordinates remain verbatim; no other map or invented offset enters.
    anchors = {}
    for name in ("VehicleOffroadHatchback", "VehicleOffroad02", "VehicleSedan02"):
        event = named.get(name.casefold())
        if event is not None:
            for point in event.findall("pos"):
                key = _point_key(point)
                if key is not None:
                    anchors.setdefault(key, point)
    if len(anchors) < 7:
        raise ValueError("At least seven authored active Avalon road sites are needed for transport.")
    ordered = sorted(anchors)
    changed = False
    for name in (*ROAD_EVENTS, MOTORBIKE_EVENT):
        current = named.get(name.casefold())
        if current is None:
            current = ET.SubElement(root, "event", name=name)
            changed = True
        count = min(POSITION_LIMIT, len(ordered))
        selected = [deepcopy(anchors[ordered[i * len(ordered) // count]]) for i in range(count)]
        before = [point.attrib for point in current.findall("pos")]
        after = [point.attrib for point in selected]
        if before != after:
            for point in list(current.findall("pos")):
                current.remove(point)
            current.extend(selected)
            changed = True
    return path, _bytes(root) if changed else None, min(POSITION_LIMIT, len(ordered))


def _events(mission):
    warnings, contained = [], []
    refs = _references(mission, EXCLUDED, warnings, contained)
    layers = _load_layers(mission, refs["events"], "event", warnings)
    if warnings:
        raise ValueError("Cannot enable transport with ambiguous events: " + "; ".join(warnings))
    root = ET.Element("events")
    for name in ROAD_EVENTS:
        rows = layers.get(name.casefold())
        if not rows:
            raise ValueError(f"Avalon does not define {name}.")
        original = _effective(rows, event=True)
        if not original.findall("children/child"):
            raise ValueError(f"{name} does not define a vehicle.")
        missing = [field for field in EVENT_FIELDS if len(original.findall(field)) != 1]
        if missing:
            raise ValueError(f"Cannot activate {name} without one complete event definition: "
                             + ", ".join(missing) + ".")
        if any(key not in original.find("flags").attrib
               for key in ("deletable", "init_random", "remove_damaged")):
            raise ValueError(f"Cannot activate {name} without complete event flags.")
        if any(not child.get("type", "").strip() for child in original.findall("children/child")):
            raise ValueError(f"Cannot activate {name} with an unnamed vehicle child.")
        # Inactive base events are absent from the native loaded-event set.
        # Reactivation must therefore supply a standalone complete definition,
        # rather than depend on an active-only override finding a loaded base.
        event = deepcopy(original)
        root.append(event)
        _set(event, "active", 1)
        # Retain authored populations. A disabled zero-target event still needs
        # a small positive target when explicitly enabled by this transport pack.
        if int(original.findtext("nominal", "0")) <= 0:
            _set(event, "nominal", 6)
            _set(event, "min", 4)
            _set(event, "max", 6)
    event = ET.SubElement(root, "event", name=MOTORBIKE_EVENT)
    for key, value in (("nominal", MOTORBIKE_TARGET), ("min", 7), ("max", MOTORBIKE_TARGET),
                       ("lifetime", 300), ("restock", 0), ("saferadius", 500),
                       ("distanceradius", 500), ("cleanupradius", 200)):
        _set(event, key, value)
    ET.SubElement(event, "flags", deletable="0", init_random="0", remove_damaged="1")
    for key, value in (("position", "fixed"), ("limit", "mixed"), ("active", 1)):
        _set(event, key, value)
    children = ET.SubElement(event, "children")
    for name in MOTORBIKES:
        ET.SubElement(children, "child", lootmax="0", lootmin="0", max="4", min="1", type=name)
    return root


def _definitions(mission):
    economy = read_economy(mission, exclude_folders=EXCLUDED)
    if economy.warnings:
        raise ValueError("Cannot enable transport with ambiguous economy: " + "; ".join(economy.warnings))
    sites = load_sites(mission)
    if sites is None:
        raise ValueError("Motorcycle spare parts need Avalon's verified building placements.")
    source = _read_xml(SOURCE / "types.xml", "types")
    root = ET.Element("types")
    for original in source.findall("type"):
        name = original.get("name", "")
        existing = economy.types.get(name.casefold())
        node = deepcopy(existing.element if existing is not None else original)
        wheel = name in tuple(f"Motorbike_0{family}_Wheel_{end}" for family in (1, 2) for end in (1, 2))
        panel = name.startswith("Motorbike_02_Shield")
        if wheel or panel:
            if not sites.supports(category="tools", usages=("Industrial", "Farm"), tags=("floor",)):
                raise ValueError("No compatible Avalon Industrial/Farm floor sites for motorcycle spares.")
            for tag in ("category", "usage", "tag", "value"):
                for child in list(node.findall(tag)):
                    node.remove(child)
            ET.SubElement(node, "category", name="tools")
            ET.SubElement(node, "tag", name="floor")
            for usage in ("Industrial", "Farm"):
                ET.SubElement(node, "usage", name=usage)
            _set(node, "nominal", max(int(node.findtext("nominal", "0")), 8 if wheel else 2))
            _set(node, "min", max(int(node.findtext("min", "0")), 6 if wheel else 1))
            _set(node, "restock", min(int(node.findtext("restock", "0")), 600))
        if existing is None or wheel or panel:
            root.append(node)
    for name in ROAD_WHEELS:
        item = economy.types.get(name.casefold())
        if item is None or not sites.proxy_groups(name):
            raise ValueError(f"Cannot enable {name} spares without its authored type and Avalon wreck proxies.")
        node = ET.SubElement(root, "type", name=item.name)
        _set(node, "nominal", max(item.nominal or 0, 10))
        _set(node, "min", max(int(item.element.findtext("min", "0")), 7))
        _set(node, "restock", min(int(item.element.findtext("restock", "0")), 600))
    spawnable = _read_xml(SOURCE / "spawnabletypes.xml", "spawnabletypes")
    if {node.get("name") for node in spawnable.findall("type")} != set(MOTORBIKES):
        raise ValueError("Bundled vanilla motorcycle attachment definitions are incomplete.")
    for node in spawnable.findall("type"):
        for group in node.findall("attachments"):
            if any(child.get("name") == "SparkPlug" for child in group.findall("item")):
                group.set("chance", "0.75")
    return root, spawnable


def _publish(mission, roots, positions):
    core_path = mission / "cfgeconomycore.xml"
    core = _read_xml(core_path, "economycore", preserve_comments=True)
    before = ET.tostring(core, encoding="unicode")
    # Native 1.30 MotorbikeScript inherits Motorbike, not CarScript. Without
    # its own CE root, its vehicles (including updated bicycle mods) are not
    # registered by the economy before their dynamic events are loaded.
    # This matches Bohemia's 1.30 cfgeconomycore.xml registration exactly.
    classes = core.findall("classes")
    if len(classes) != 1:
        raise ValueError("Avalon economy core needs one root-class section for motorcycles.")
    motorcycle_roots = [node for node in classes[0].findall("rootclass")
                        if node.get("name", "").casefold() == "motorbikescript"]
    if len(motorcycle_roots) > 1:
        raise ValueError("Duplicate MotorbikeScript economy root registrations.")
    if motorcycle_roots:
        if motorcycle_roots[0].get("name") != "MotorbikeScript" or motorcycle_roots[0].get("act") != "car":
            raise ValueError('MotorbikeScript economy root must use its exact class name and act="car".')
    else:
        ET.SubElement(classes[0], "rootclass", name="MotorbikeScript", act="car", reportMemoryLOD="no")
    previous_nodes = [ce for ce in core.findall("ce")
                      if ce.get("folder", "").replace("\\", "/").rstrip("/").casefold() == FOLDER]
    if len(previous_nodes) > 1:
        raise ValueError("Duplicate managed land transport economy references.")
    previous_index = list(core).index(previous_nodes[0]) if previous_nodes else None
    for ce in previous_nodes:
        core.remove(ce)
    extension = ET.Element("ce", folder=FOLDER)
    extension.tail = "\n"
    for kind in roots:
        ET.SubElement(extension, "file", name=kind + ".xml", type=kind)
    following = next((n for n in core.findall("ce") if n.get("folder", "").casefold()
                      in {"azimuth_economy_repairs", "azimuth_loot_balance"}), None)
    if previous_index is not None:
        core.insert(previous_index, extension)
    elif following is None:
        core.append(extension)
    else:
        core.insert(list(core).index(following), extension)
    folder = mission / FOLDER
    folder.mkdir(exist_ok=True)
    if not stat.S_ISDIR(source_type(folder)):
        raise ValueError("Land transport economy folder must be an ordinary directory.")
    previous = {}
    try:
        payloads = {folder / (kind + ".xml"): _bytes(root) for kind, root in roots.items()}
        path, data, _ = positions
        if data is not None:
            payloads[path] = data
        for path, data in payloads.items():
            if path.exists() or path.is_symlink():
                if not stat.S_ISREG(source_type(path)):
                    raise ValueError("Land transport output must be an ordinary file.")
            old = path.read_bytes() if path.is_file() else None
            if old != data:
                previous[path] = old
                atomic_text(path, data.decode("utf-8"))
        if ET.tostring(core, encoding="unicode") != before:
            _write_core(core_path, core)
    except (OSError, ValueError):
        for path, old in previous.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_text(path, old.decode("utf-8"))
        raise


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    """Register native transport; caller must use matching DayZ 1.30+ binaries."""
    mission = mission.resolve()
    positions = _positions(mission)
    types, spawnable = _definitions(mission)
    events = _events(mission)
    _publish(mission, {"types": types, "spawnabletypes": spawnable, "events": events}, positions)
    return ("Olga, Gunter and M3S truck spawns are enabled using the active Avalon road-site pool and matching wheel spares.",
            f"Seven native motorcycle variants are enabled: target {MOTORBIKE_TARGET} across {positions[2]} Avalon road sites. "
            "Both wheels are fitted; spark plugs have a 75% attachment chance. Fuel and replacement parts remain scavenged.")
