"""Add Expansion transport using exact publisher classes and Avalon anchors.

No Workshop binaries are repacked. The manager loads the signed upstream mods.
Position checks establish map evidence, not native rotor/terrain clearance.
"""
from __future__ import annotations
from azimuth_app.paths import APP_ROOT

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import stat
from statistics import median
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_economy import MAX_XML_BYTES, register_bytes
from azimuth_app.economy.avalon_loot_audit import read_economy
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.world.avalon_immersion import _restore_positions
from azimuth_app.core.backups import source_type
from azimuth_app.economy.hunter_economy import _read_xml
from azimuth_app.core.worlds import atomic_text

CF = "1559212036"
DABS = "2545327648"
CORE = "2291785308"
LICENSED = "2116157322"
VEHICLES = "2291785437"
ITEMS = ((CF, "CF"), (DABS, "Dabs Framework"), (CORE, "DayZ-Expansion-Core"),
         (LICENSED, "DayZ-Expansion-Licensed"), (VEHICLES, "DayZ-Expansion-Vehicles"))
SLUG = "azimuth_expansion_transport"
FOLDER = "azimuth_workshop/" + SLUG
BUNDLE = Path("Custom/Transport/Expansion")
PREFIX = "VehicleAzimuthExpansion"
HELIPAD = "Land_dego_helipad"
ROAD_EVENTS = ("VehicleCivilianSedan", "VehicleHatchback02", "VehicleOffroadHatchback", "VehicleOffroad02", "VehicleSedan02")


def _bytes(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _ordinary(path: Path, directory: bool = False) -> None:
    kind = source_type(path)
    if not (stat.S_ISDIR(kind) if directory else stat.S_ISREG(kind)):
        raise ValueError(f"Expected an ordinary {'folder' if directory else 'file'}: {path.name}.")


def _data() -> tuple[dict, dict[str, ET.Element]]:
    root = APP_ROOT
    folder = root / BUNDLE
    for part in (root / 'Custom', root / 'Custom/Transport', folder):
        _ordinary(part, True)
    catalog_path = folder / 'catalog.json'
    _ordinary(catalog_path)
    if catalog_path.stat().st_size > 128 * 1024:
        raise ValueError('Expansion transport catalog is too large.')
    catalog = json.loads(catalog_path.read_text(encoding='utf-8-sig'))
    if catalog.get('schema_version') != 1 or len(catalog.get('families', [])) != 11:
        raise ValueError('Expansion transport catalog is invalid.')
    result = {}
    for kind, expected in (('types', 'types'), ('events', 'events'), ('spawnabletypes', 'spawnabletypes')):
        path = folder / (kind + '.xml')
        _ordinary(path)
        data = path.read_bytes()
        if len(data) > MAX_XML_BYTES or sha256(data).hexdigest() != catalog['data_sha256'].get(path.name):
            raise ValueError(f'Expansion transport source check failed: {path.name}.')
        result[kind] = _read_xml(path, expected)
    return catalog, result


def _coordinates(node: ET.Element) -> tuple[float, float] | None:
    try:
        x, z = float(node.get('x', '')), float(node.get('z', ''))
        other = [float(node.get('a', '0'))]
        if node.get('y') is not None:
            other.append(float(node.get('y')))
        if x <= 0 or z <= 0 or not all(math.isfinite(n) for n in (x, z, *other)):
            return None
        return round(x, 4), round(z, 4)
    except (TypeError, ValueError):
        return None


def _anchors(root: ET.Element, names: tuple[str, ...]) -> list[ET.Element]:
    positions = {}
    for name in names:
        matches = [e for e in root.findall('event') if e.get('name', '').casefold() == name.casefold()]
        if len(matches) > 1:
            raise ValueError(f'Duplicate Avalon placement event {name}.')
        if matches:
            for node in matches[0].findall('pos'):
                point = _coordinates(node)
                if point is not None:
                    positions.setdefault(point, deepcopy(node))
    return [positions[key] for key in sorted(positions)]


def _helipads(mission: Path) -> tuple[list[ET.Element], dict]:
    proto = _read_xml(mission / 'mapgroupproto.xml', 'prototype')
    found = [g for g in proto.findall('group') if g.get('name') == HELIPAD]
    if len(found) != 1:
        raise ValueError('Avalon helicopter placement needs one unambiguous authored helipad prototype.')
    surface = []
    for point in found[0].findall("container[@name='lootFloor']/point"):
        try:
            values = [float(n) for n in point.get('pos', '').split()]
            if len(values) == 3 and all(math.isfinite(n) for n in values) and 0 <= values[1] <= 3:
                surface.append(values[1])
        except ValueError:
            pass
    if not surface:
        raise ValueError('Avalon helipad floor-height evidence is missing.')
    surface_y = median(surface)
    placements = _read_xml(mission / 'mapgrouppos.xml', 'map')
    groups = []
    for group in placements.findall('group'):
        try:
            point = tuple(float(n) for n in group.get('pos', '').split())
            angles = tuple(float(n) for n in group.get('rpy', '0 0 0').split())
            if len(point) != 3 or len(angles) != 3 or not all(math.isfinite(n) for n in (*point, *angles)):
                continue
            groups.append((group, point, angles))
        except ValueError:
            continue
    candidates = []
    rejected = []
    seen = set()
    for group, point, angles in groups:
        if group.get('name') != HELIPAD:
            continue
        x, y, z = point
        key = (round(x, 4), round(z, 4))
        if key in seen:
            raise ValueError('Duplicate Avalon helipad placement coordinate.')
        seen.add(key)
        # This only rejects obvious conflicts in the authored map-group data.
        # Props, foliage, model extents and native collision remain game checks.
        clearance = min((math.hypot(x - other[0], z - other[2])
                         for other_group, other, _ in groups if other_group.get('name') != HELIPAD), default=0)
        if x <= 0 or z <= 0 or abs(angles[0]) > 2 or abs(angles[1]) > 2 or clearance < 20:
            rejected.append({'x': x, 'z': z, 'nearest_other_origin_m': round(clearance, 2)})
            continue
        # Prototype floor points establish the pad surface; one metre above it
        # gives the physics body settling space. This is not a surveyed model fit.
        node = ET.Element('pos', x=f'{x:.6f}', y=f'{y + surface_y + 1.0:.6f}',
                          z=f'{z:.6f}', a=group.get('a', '0'))
        if _coordinates(node) is not None:
            candidates.append((clearance, key, node))
    candidates.sort(key=lambda value: (-value[0], value[1]))
    if len(candidates) < 4:
        raise ValueError('Avalon has fewer than four usable authored helipad anchors; no helicopter coordinates were guessed.')
    return [item[2] for item in candidates], {
        'authored_helipads': len(seen), 'accepted_anchors': len(candidates),
        'rejected_anchors': rejected, 'prototype_floor_offset_m': surface_y,
        'placement_check': 'Authored helipad centres with floor-height evidence and 20 m origin clearance; native rotor/terrain clearance not tested.',
    }


def _spread(points: list[ET.Element], maximum: int) -> list[ET.Element]:
    count = min(len(points), maximum)
    return [deepcopy(points[index * len(points) // count]) for index in range(count)] if count else []


def _positions(mission: Path, catalog: dict) -> tuple[Path, bytes, dict, dict[str, int]]:
    path = mission / 'cfgeventspawns.xml'
    root = _read_xml(path, 'eventposdef', preserve_comments=True)
    originals = [node for node in root.findall('event') if node.get('name', '').startswith(PREFIX)]
    names = [node.get('name') for node in originals]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate managed Expansion placement event.')
    road = _anchors(root, ROAD_EVENTS)
    truck = _anchors(root, ('VehicleTruck01',))
    water = _anchors(root, ('VehicleBoat',))
    air, air_report = _helipads(mission)
    families = catalog['families']
    routes = {route: [f for f in families if f['route'] == route] for route in ('road', 'truck', 'water')}
    allocation = {}
    for route, points in (('road', road), ('truck', truck), ('water', water)):
        for index, family in enumerate(routes[route]):
            selected = _spread(points[index::len(routes[route])], family['target'] * 3)
            if not selected:
                raise ValueError(f"Avalon lacks authored {route} positions for {family['label']}.")
            allocation[family['event']] = selected
    # Give the largest rotorcraft the clearest point first; no shared helicopter
    # event anchor and no edits to publisher crash locations.
    sequence = ('Merlin', 'UH1H', 'MH6', 'Gyrocopter', 'UH1H', 'MH6')
    for point, label in zip(air, sequence):
        name = PREFIX + label
        allocation.setdefault(name, []).append(point)
    existing = {node.get('name'): node for node in originals}
    counts = {}
    for family in families:
        points = allocation.get(family['event'], [])
        if not points:
            raise ValueError(f"No Avalon placement anchors for {family['label']}.")
        event = existing.get(family['event'])
        if event is None:
            event = ET.SubElement(root, 'event', name=family['event'])
        else:
            for old in list(event.findall('pos')):
                event.remove(old)
        for point in points:
            event.append(deepcopy(point))
        counts[family['event']] = len(points)
    report = {'helicopters': air_report, 'source_road_positions': len(road),
              'source_truck_positions': len(truck), 'source_water_positions': len(water),
              'event_positions': {name: [dict(p.attrib) for p in points] for name, points in allocation.items()},
              'native_vehicle_clearance': 'NOT_TESTED; authored anchors are reused, not a live geometry survey'}
    return path, _bytes(root), report, counts


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    for item_id, label in ITEMS:
        if item_id not in mods or not mods[item_id].is_dir():
            raise ValueError(f'Transport requires the original Workshop {label} ({item_id}).')
    mission = mission.resolve()
    catalog, data = _data()
    economy = read_economy(mission, exclude_folders=(FOLDER, 'azimuth_loot_balance', 'azimuth_economy_repairs'))
    if economy.warnings:
        raise ValueError('Cannot add Expansion transport to an invalid economy: ' + '; '.join(economy.warnings))
    sites = load_sites(mission)
    if sites is None:
        raise ValueError('Expansion spare parts need verifiable Avalon building placements.')
    notices = []
    # Existing explicit publisher/user definitions remain authoritative. Never
    # create duplicate class definitions through the normal registration helper.
    types = data['types']
    for node in list(types.findall('type')):
        name = node.get('name')
        if name.casefold() in economy.types:
            types.remove(node)
            notices.append(f'Expansion transport kept existing economy definition for {name}.')
            continue
        if int(node.findtext('nominal', '0')) > 0:
            category = node.find('category')
            if not sites.supports(category=category.get('name') if category is not None else None,
                                  usages=sites.flags(node, 'usage'), tags=sites.flags(node, 'tag')):
                raise ValueError(f'No authored Avalon loot route for required transport part {name}.')
    path, positions, report, counts = _positions(mission, catalog)
    for event in data['events'].findall('event'):
        capacity = counts[event.get('name')]
        nominal = min(int(event.findtext('nominal')), capacity)
        for tag, value in (('nominal', nominal), ('min', max(1, nominal - 1)), ('max', nominal)):
            event.find(tag).text = str(value)
        for child in event.findall('children/child'):
            child.set('max', str(nominal))
    # Validate attachment identities before publishing any file. Rows absent from
    # the mod's original CE examples were added only from exact author sources.
    known = set(economy.types) | {node.get('name').casefold() for node in types.findall('type')}
    for entry in data['spawnabletypes'].findall('type'):
        for item in entry.findall('.//item'):
            if item.get('name', '').casefold() not in known:
                raise ValueError('Expansion transport attachment lacks an economy definition: ' + item.get('name', ''))
    old_position_bytes = path.read_bytes()
    old_positions = old_position_bytes.decode('utf-8-sig')
    text = positions.decode('utf-8')
    try:
        if text != old_positions:
            atomic_text(path, text)
        register_bytes(mission, SLUG, {kind: (kind + '.xml', _bytes(root)) for kind, root in data.items()})
    except (OSError, ValueError):
        if text != old_positions:
            _restore_positions(path, old_position_bytes)
        raise
    destination = profiles / 'Azimuth'
    destination.mkdir(parents=True, exist_ok=True)
    _ordinary(destination, True)
    report.update(schema_version=1, vehicle_variants=catalog['vehicle_variants'],
                  families=[{'label': f['label'], 'event': f['event'], 'positions': counts[f['event']],
                             'target': min(f['target'], counts[f['event']]), 'variants': f['variants']}
                            for f in catalog['families']],
                  third_party_binaries='Loaded from signed original Workshop downloads, not included in Azimuth',
                  world_save_changes='None; Central Economy creates new vehicles over normal cycles')
    report_path = destination / 'Expansion_Transport_Audit.json'
    if report_path.exists() or report_path.is_symlink():
        _ordinary(report_path)
    report_text = json.dumps(report, indent=2) + '\n'
    if not report_path.exists() or report_path.read_text(encoding='utf-8-sig') != report_text:
        atomic_text(report_path, report_text)
    return tuple(notices) + (
        f"Expansion transport prepared: {catalog['vehicle_variants']} variants, including helicopters, gyrocopter, UAZ, Land Rover, Vodnik, bus, tractor and boats.",
        'Helicopters use authored Avalon helipad anchors; actual rotor clearance and vehicle handling still need an in-game check.',
    )
