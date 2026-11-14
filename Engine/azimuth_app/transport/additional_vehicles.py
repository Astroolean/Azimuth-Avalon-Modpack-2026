"""Install the exported Workshop vehicle definitions as one bounded Avalon CE layer.

Only signed, already downloaded optional packs selected by the launcher are used.
The catalog contains literal classes and compatible attachment slots from the
user's installed configs. No existing vehicles, saves or delivery receipts are
changed. Large vessels and airships require dedicated authored placement lists.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import stat
from xml.etree import ElementTree as ET

from azimuth_app.paths import APP_ROOT
from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text
from azimuth_app.economy.avalon_economy import register_bytes
from azimuth_app.economy.avalon_loot_audit import read_economy
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.economy.verified_publisher_loot import (verified_rows, ordinary_target, supported_on_map, remembered_rows, correct_two_vehicle_spare_gaps)
from azimuth_app.economy.hunter_economy import _read_xml, _write_core
from azimuth_app.world.avalon_immersion import _restore_positions
from azimuth_app.transport.avalon_air_transport import _anchors, _coordinates, _helipads, _spread
from azimuth_app.transport.vehicle_workshop import ITEMS as DOWNLOAD_ITEMS

READY_IDS = frozenset(row[0] for row in DOWNLOAD_ITEMS)
ITEMS = tuple((row[0], row[1]) for row in DOWNLOAD_ITEMS if row[0] in READY_IDS)
REQUIRED_DEPENDENCIES = {
    '2651195301': ('1559212036', '2291785308', '2116157322', '2291785437'),
    '2971993210': ('2971190303',),
}
SLUG = 'azimuth_additional_vehicles'
FOLDER = 'azimuth_workshop/' + SLUG
PREFIX = 'VehicleAzimuthWorkshop'
CATALOG = Path('Custom/Transport/Workshop/catalog.json')
ROUTE_TARGETS = {'light': 8, 'road': 10, 'truck': 3, 'water': 3, 'air': 2,
                 'large_water': 1, 'airship': 1, 'plane': 1}
EVENTS = {route: PREFIX + ''.join(word.title() for word in route.split('_')) for route in ROUTE_TARGETS}
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
ROOTS = {'CarScript', 'BoatScript', 'MotorbikeScript'}
BASE_PARTS = frozenset(name.casefold() for name in (
    'CarBattery', 'TruckBattery', 'CarRadiator', 'SparkPlug', 'GlowPlug',
    'HeadlightH7', 'HatchbackWheel', 'ExpansionHelicopterBattery',
    'ExpansionIgniterPlug', 'ExpansionHydraulicHoses',
))


def _ordinary(path: Path, directory: bool = False) -> None:
    mode = source_type(path)
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError(f'Additional vehicle output must be an ordinary {"folder" if directory else "file"}: {path.name}.')


def _bytes(root: ET.Element) -> bytes:
    ET.indent(root)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'


def _catalog() -> dict:
    path = APP_ROOT / CATALOG
    _ordinary(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Additional vehicle catalog is too large.')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema_version') != 1 or not isinstance(data.get('packs'), list):
        raise ValueError('Additional vehicle catalog has an unsupported format.')
    seen = set()
    for pack in data['packs']:
        ident = pack.get('id')
        if ident not in {row[0] for row in DOWNLOAD_ITEMS} or ident in seen:
            raise ValueError('Additional vehicle catalog has an unknown or repeated Workshop item.')
        seen.add(ident)
        part_names = {p['class'].casefold() for p in pack.get('parts', [])}
        if len(part_names) != len(pack.get('parts', [])):
            raise ValueError('Additional vehicle catalog has duplicate parts.')
        for part in pack.get('parts', []):
            if not IDENTIFIER.fullmatch(part['class']) or not part.get('slots'):
                raise ValueError('Additional vehicle catalog has an invalid part.')
        for item in pack.get('support_items', []):
            if (pack['id'] != '3810689113' or item.get('class') != 'AirshipRemote'
                    or item.get('kind') != 'remote'):
                raise ValueError('Additional vehicle catalog has an unsupported auxiliary item.')
        vehicle_names = set()
        for vehicle in pack.get('vehicles', []):
            name = vehicle.get('class', '')
            if not IDENTIFIER.fullmatch(name) or name.casefold() in vehicle_names or vehicle.get('route') not in ROUTE_TARGETS or vehicle.get('root') not in ROOTS:
                raise ValueError('Additional vehicle catalog has an invalid or repeated vehicle.')
            vehicle_names.add(name.casefold())
            slots = set()
            for part in vehicle.get('attachments', []):
                name, slot = part.get('class', ''), part.get('slot', '')
                if not IDENTIFIER.fullmatch(name) or not IDENTIFIER.fullmatch(slot) or slot.casefold() in slots:
                    raise ValueError('Additional vehicle catalog has a repeated or invalid attachment slot.')
                slots.add(slot.casefold())
                if name.casefold() not in part_names | BASE_PARTS:
                    raise ValueError('Additional vehicle catalog references an unknown attachment: ' + name)
                match = next((p for p in pack.get('parts', []) if p['class'].casefold() == name.casefold()), None)
                if match is not None and slot.casefold() not in {s.casefold() for s in match['slots']}:
                    raise ValueError('Additional vehicle attachment does not fit its configured slot: ' + name)
            publisher_assembled = (pack['id'] == '3810689113' and vehicle['class'] == 'Airship'
                                   and vehicle.get('assembly') == 'publisher_on_spawn'
                                   and vehicle.get('assembly_evidence', {}).get('source') ==
                                   'https://steamcommunity.com/sharedfiles/filedetails/?id=3810689113')
            if not publisher_assembled and any(slot.casefold() not in slots for slot in vehicle.get('wheel_slots', [])):
                raise ValueError('Additional vehicle catalog leaves a physics wheel slot empty: ' + vehicle['class'])
    return data


def _type(name: str, *, spare: bool = False, vehicle: bool = False) -> ET.Element:
    node = ET.Element('type', name=name)
    for key, value in (('nominal', 1 if spare else 0), ('lifetime', 3888000 if vehicle else 28800),
                       ('restock', 600), ('min', 0), ('quantmin', -1), ('quantmax', -1), ('cost', 100)):
        ET.SubElement(node, key).text = str(value)
    ET.SubElement(node, 'flags', count_in_cargo='0', count_in_hoarder='0', count_in_map='1',
                  count_in_player='0', crafted='0', deloot='0')
    if spare:
        ET.SubElement(node, 'category', name='tools')
        ET.SubElement(node, 'tag', name='floor')
        for usage in ('Industrial', 'Farm'):
            ET.SubElement(node, 'usage', name=usage)
    return node


def _spawnable(vehicle: dict) -> ET.Element:
    node = ET.Element('type', name=vehicle['class'])
    # Independent groups fit every wheel/required part, including repeats of the
    # same wheel class. A single group of four wheels would select only one.
    for part in vehicle['attachments']:
        group = ET.SubElement(node, 'attachments', chance='1.00')
        ET.SubElement(group, 'item', name=part['class'], chance='1.00')
    return node


def _event(route: str, vehicles: list[dict], count: int) -> ET.Element:
    node = ET.Element('event', name=EVENTS[route])
    for key, value in (('nominal', count), ('min', max(1, count - 1)), ('max', count),
                       ('lifetime', 3888000), ('restock', 600), ('saferadius', 100),
                       ('distanceradius', 100), ('cleanupradius', 200)):
        ET.SubElement(node, key).text = str(value)
    ET.SubElement(node, 'flags', deletable='0', init_random='0', remove_damaged='1')
    for key, value in (('position', 'fixed'), ('limit', 'mixed'), ('active', 1)):
        ET.SubElement(node, key).text = str(value)
    children = ET.SubElement(node, 'children')
    # Zero per-child minima keep the population bounded regardless of how many
    # paint variants the author ships. Every literal variant remains eligible.
    for vehicle in vehicles:
        ET.SubElement(children, 'child', type=vehicle['class'], min='0', max=str(count), lootmin='0', lootmax='0')
    return node


def _positions(mission: Path, routes: set[str], profiles: Path | None = None) -> tuple[Path, ET.Element, dict[str, list[ET.Element]], list[str]]:
    path = mission / 'cfgeventspawns.xml'
    root = _read_xml(path, 'eventposdef', preserve_comments=True)
    for name in EVENTS.values():
        if len([n for n in root.findall('event') if n.get('name', '').casefold() == name.casefold()]) > 1:
            raise ValueError('Duplicate additional vehicle placement event: ' + name)
    # Only Avalon's active author pools are accepted. Disabled Chernarus lists
    # from the map template are not suitable coordinates for this map.
    road = _anchors(root, ('VehicleOffroadHatchback', 'VehicleOffroad02', 'VehicleSedan02'))
    water = _anchors(root, ('VehicleBoat',))
    pools = {'light': _spread(road, 32), 'road': _spread(road, 40),
             'truck': _spread(road, 16),
             'water': _spread(water, 12)}
    notices = []
    if 'plane' in routes:
        from azimuth_app.transport.lm_plane_positions import positions as plane_positions
        pools['plane'] = plane_positions(mission, root)
    surveyed = {}
    if profiles is not None:
        from azimuth_app.transport.transport_survey import validated_sites
        surveyed = validated_sites(mission, profiles)
    if 'air' in routes:
        try:
            air, _ = _helipads(mission)
            # Expansion already allocates the first six accepted pad anchors.
            # Use separate measured sites instead of oversubscribing those pads.
            pools['air'] = air[6:] + surveyed.get('air', [])
            if not pools['air']:
                pools['air'] = _anchors(root, (EVENTS['air'],))
                if pools['air']:
                    notices.append('Existing additional-helicopter positions were retained while separate surveyed sites are pending.')
        except (OSError, ValueError) as error:
            pools['air'] = surveyed.get('air', [])
            notices.append('Additional helicopter locations were not changed: ' + str(error))
    dedicated = set()
    for route in ('large_water', 'airship'):
        # A dedicated existing list is explicit map-placement data. No offset
        # from a dinghy, road car or helicopter is invented for these models.
        pools[route] = _anchors(root, (EVENTS[route],))
        if pools[route]:
            dedicated.add(route)
        else:
            pools[route] = surveyed.get(route, [])
    selected = {}
    for route in routes:
        points = pools.get(route, [])
        selected[route] = points
        if not points:
            label = {'large_water': 'Large boats and galleons', 'airship': 'Airships'}.get(route, route.title() + ' vehicles')
            notices.append(label + ' are loaded, but no suitable authored Avalon placement list is available; no unsafe spawn was guessed.')
            continue
        if route in dedicated:
            continue  # Keep dedicated user-authored positions byte-semantically intact.
        matches = [n for n in root.findall('event') if n.get('name') == EVENTS[route]]
        event = matches[0] if matches else ET.SubElement(root, 'event', name=EVENTS[route])
        for node in list(event.findall('pos')):
            event.remove(node)
        event.extend(deepcopy(points))
    return path, root, selected, notices


def _root_classes(mission: Path, required: set[str]) -> None:
    path = mission / 'cfgeconomycore.xml'
    root = _read_xml(path, 'economycore', preserve_comments=True)
    sections = root.findall('classes')
    if len(sections) != 1:
        raise ValueError('Avalon economy core needs one root-class section.')
    changed = False
    for name in sorted(required):
        found = [n for n in sections[0].findall('rootclass') if n.get('name', '').casefold() == name.casefold()]
        if len(found) > 1 or (found and (found[0].get('name') != name or found[0].get('act') != 'car')):
            raise ValueError('Conflicting vehicle economy root: ' + name)
        if not found:
            ET.SubElement(sections[0], 'rootclass', name=name, act='car', reportMemoryLOD='no')
            changed = True
    if changed:
        _write_core(path, root)


def require_saved_packs(profiles: Path, mod_ids) -> None:
    """Do not start a saved world with a previously enabled vehicle pack absent."""
    path = profiles / 'Azimuth' / 'Additional_Vehicle_Transport_Audit.json'
    if not path.exists() and not path.is_symlink():
        return
    _ordinary(path)
    if path.stat().st_size > 512 * 1024:
        raise ValueError('The saved additional-vehicle record is too large; it was preserved.')
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        rows = data['loaded_packs']
        if data.get('schema_version') != 1 or not isinstance(rows, list) or len(rows) > 64:
            raise ValueError
        identifiers = [row['id'] for row in rows]
        if any(not isinstance(value, str) or not value.isdigit() for value in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('The saved additional-vehicle record is invalid; it was preserved. Restore that record before launching this world.') from None
    available = set(mod_ids)
    missing = [row for row in rows if row['id'] not in available]
    if missing:
        labels = ', '.join(str(row.get('label') or row['id']) + ' (' + row['id'] + ')' for row in missing)
        raise ValueError('This save previously used ' + labels + '. Finish or restore those Steam Workshop downloads before starting Avalon, so existing vehicles remain available.')


def prepare(mission: Path, profiles: Path, mods: dict[str, Path]) -> tuple[str, ...]:
    """Publish only configured optional mods; caller has resolved dependencies."""
    require_saved_packs(profiles, {identity for identity, path in mods.items() if path.is_dir()})
    mission = mission.resolve()
    data = _catalog()
    packs = [pack for pack in data['packs'] if pack.get('load') and pack['id'] in READY_IDS
             and pack['id'] in mods and mods[pack['id']].is_dir()]
    economy = read_economy(mission, exclude_folders=(FOLDER, 'azimuth_economy_repairs', 'azimuth_loot_balance'))
    if economy.warnings:
        raise ValueError('Cannot configure additional vehicles in an ambiguous economy: ' + '; '.join(economy.warnings))
    vehicles = [v for pack in packs for v in pack['vehicles']]
    # Cross-pack duplicate concrete classes are rejected rather than silently
    # changing which author's physics/parts the event would instantiate.
    names = [v['class'].casefold() for v in vehicles]
    if len(names) != len(set(names)):
        raise ValueError('Additional vehicle packs contain a repeated concrete vehicle class.')
    routes = {v['route'] for v in vehicles}
    path, positions, pools, notices = _positions(mission, routes, profiles)
    held = [{'pack': pack['label'], **item} for pack in packs for item in pack.get('excluded', [])
            if not item['reason'].startswith('Administrative,')]
    for pack in packs:
        pending = [item for item in pack.get('excluded', []) if not item['reason'].startswith('Administrative,')]
        if pending:
            notices.append(pack['label'] + ': ' + str(len(pending)) + ' vehicle definitions lack a complete spawn recipe in the supplied configuration; their natural spawn was not guessed.')
        issues = pack.get('asset_issues', [])
        unresolved = [issue for issue in issues if not issue.get('repair')]
        repaired = [issue for issue in issues if issue.get('repair')]
        if unresolved:
            notices.append(pack['label'] + ': ' + str(len(unresolved)) + ' original asset references are absent from the supplied PBO index; details are recorded in the transport audit.')
        if repaired:
            notices.append(pack['label'] + ': source-backed compatibility fixes applied to ' + str(len(repaired)) + ' original asset references; details are recorded in the transport audit.')
    types, spawnables, events = ET.Element('types'), ET.Element('spawnabletypes'), ET.Element('events')
    defined = set(economy.types)
    sites = load_sites(mission)
    spares_supported = bool(sites and sites.supports(category='tools', usages=('Industrial', 'Farm'), tags=('floor',)))
    # Evidence-bound, additive publisher spares. No new world events, recipe
    # modifications, cosmetics, vehicles or Workshop PBO modifications.
    # Retain the previously generated values if an author later updates their
    # setup XML, rather than quietly removing working spare-part loot.
    source_imports = {}
    unplaced_authored = []
    imported_source_names = set()
    vehicle_classes = {v['class'].casefold() for v in vehicles}
    for pack in packs:
        if pack['id'] not in {'2880254695', '3354681846'}:
            continue
        approved, messages = verified_rows(pack['id'], mods[pack['id']])
        notices.extend(messages)
        if not approved:
            approved = remembered_rows(mission, SLUG, evidence_id=pack['id'])
        installed = 0
        for key, source_row in approved.items():
            if key in defined or key in vehicle_classes or key in imported_source_names:
                continue
            # The publisher must affirm natural loot and the Avalon map must
            # have at least one matching category/usage/loot placement point.
            if not ordinary_target(source_row):
                continue
            if not supported_on_map(source_row, sites):
                adjusted = correct_two_vehicle_spare_gaps(source_row, sites) if messages == () else None
                if adjusted is not None:
                    source_row = adjusted
                    notices.append('Verified publisher spare placement repaired: ' + source_row.get('name', ''))
                elif any(p['class'].casefold() == key and p['kind'] == 'wheel' for p in pack['parts']):
                    # Existing authored Industrial/Farm one-per-wheel route
                    # already applies below; do not duplicate it.
                    continue
                else:
                    unplaced_authored.append(source_row.get('name', ''))
                    continue
            types.append(deepcopy(source_row))
            defined.add(key)
            imported_source_names.add(key)
            installed += 1
        source_imports[pack['id']] = installed
    if unplaced_authored:
        notices.append(str(len(unplaced_authored)) + ' positive publisher spare classes have no matching Avalon static placement; their natural spawn was not guessed: ' + ', '.join(sorted(unplaced_authored)[:8]))
    for pack in packs:
        for part in pack['parts']:
            key = part['class'].casefold()
            if key in defined:
                continue
            # Every body part remains persistent and can be salvaged from a
            # complete spawned vehicle. Keep ordinary wheel loot to one per
            # concrete class so hundreds of paint variants cannot flood loot.
            types.append(_type(part['class'], spare=spares_supported and part['kind'] == 'wheel'))
            defined.add(key)
        for item in pack.get('support_items', []):
            key = item['class'].casefold()
            if key not in defined:
                types.append(_type(item['class'], spare=spares_supported))
                defined.add(key)
    configured, deferred = [], []
    for vehicle in vehicles:
        key = vehicle['class'].casefold()
        if key not in defined:
            types.append(_type(vehicle['class'], vehicle=True))
            defined.add(key)
        spawnables.append(_spawnable(vehicle))
        (configured if pools.get(vehicle['route']) else deferred).append(vehicle['class'])
    for route in sorted(routes):
        points = pools.get(route, [])
        if points:
            events.append(_event(route, [v for v in vehicles if v['route'] == route], min(ROUTE_TARGETS[route], len(points))))
    # Stable startup report ordering, independent of Python set hash seed.
    notices = sorted(dict.fromkeys(notices))
    report = {'schema_version': 1, 'evidence_created_utc': data['evidence_created_utc'],
              'loaded_packs': [{'id': p['id'], 'label': p['label']} for p in packs],
              'configured_variants': configured, 'without_natural_placement': deferred,
              'without_complete_spawn_definition': held,
              'asset_issues': [{'pack': p['label'], **issue} for p in packs for issue in p.get('asset_issues', [])],
              'events': [{'name': e.get('name'), 'target': int(e.findtext('nominal')),
                          'variant_count': len(e.findall('children/child'))} for e in events],
              'spares_have_authored_sites': spares_supported, 'publisher_verified_spares': source_imports,
              'unplaced_publisher_spares': unplaced_authored,
              'notices': notices, 'validation': data['validation'], 'existing_save_changes': 'None'}
    folder = profiles / 'Azimuth'
    folder.mkdir(parents=True, exist_ok=True)
    _ordinary(folder, True)
    report_path = folder / 'Additional_Vehicle_Transport_Audit.json'
    # All changes remain in our own layer or our own placement events. Keep one
    # transaction across core, positions, CE files and the saved-pack record.
    # A failed audit write must not erase the evidence needed to protect a save.
    touched = [path, mission / 'cfgeconomycore.xml', report_path]
    touched += [mission / FOLDER / (kind + '.xml') for kind in ('types', 'events', 'spawnabletypes')]
    before = {}
    for target in touched:
        if target.exists() or target.is_symlink():
            _ordinary(target)
            before[target] = target.read_bytes()
        else:
            before[target] = None
    try:
        _root_classes(mission, {v['root'] for v in vehicles})
        new_positions = _bytes(positions)
        if new_positions != before[path]:
            atomic_text(path, new_positions.decode('utf-8'))
        register_bytes(mission, SLUG, {kind: (kind + '.xml', _bytes(root)) for kind, root in
                                     (('types', types), ('events', events), ('spawnabletypes', spawnables))})
        report_text = json.dumps(report, indent=2) + '\n'
        if before[report_path] != report_text.encode('utf-8'):
            atomic_text(report_path, report_text)
    except (OSError, ValueError):
        for target, payload in before.items():
            if payload is None:
                target.unlink(missing_ok=True)
            elif not target.exists() or target.read_bytes() != payload:
                _restore_positions(target, payload)
        raise
    summary = (f'Additional transport: {len(packs)} downloaded packs loaded; {len(configured)} variants in bounded Avalon spawn pools.',)
    if not spares_supported and packs:
        notices.append('Additional vehicles spawn with their configured parts; separate wheel loot needs matching Avalon Industrial/Farm floor sites.')
    return summary + tuple(notices)
