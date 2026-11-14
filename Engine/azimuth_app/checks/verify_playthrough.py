"""Read-only checks of the installed Avalon mission, not an in-game spawn test."""
from __future__ import annotations
from azimuth_app.paths import APP_ROOT

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
sys.dont_write_bytecode = True
from xml.etree import ElementTree as ET

from azimuth_app.economy.avalon_loot_audit import (
    read_economy, _references, _load_layers, _effective, _xml,
)
from azimuth_app.economy.avalon_loot_sites import load_sites
from azimuth_app.world.avalon_atmosphere import FISH_ECONOMY_FOLDER, FISH_GENERATED
from azimuth_app.world.avalon_building import beehive_setup_notices
from azimuth_app.economy.mission_xml import parse_mission_xml
from azimuth_app.cosmetics import skin_settings
from azimuth_app.world import avalon_workshop
from azimuth_app.checks.verify_gun_skins import check_sources


def _number(node, field):
    value = node.findtext(field)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f'{field} is not a whole number: {value!r}') from None


def _check_event_inputs(mission: Path, errors: list[str], warnings: list[str]) -> dict:
    """Validate CE definitions independently of ordinary item target checks."""
    reference_errors, contained_errors = [], []
    refs = _references(mission, set(), reference_errors, contained_errors)
    layers = _load_layers(mission, refs["events"], "event", reference_errors)
    errors.extend(line for line in (*reference_errors, *contained_errors) if line not in errors)
    events = [_effective(group, event=True) for group in layers.values()]
    active_vehicles = []
    for event in events:
        name = event.get("name", "")
        values = {}
        for field in ("nominal", "min", "max", "lifetime", "restock", "saferadius", "distanceradius", "cleanupradius"):
            try:
                value = _number(event, field)
                values[field] = value
                if value is None or value < 0:
                    errors.append(f"Event {name}: {field} is missing or negative.")
            except ValueError as error:
                errors.append(f"Event {name}: {error}")
        active = event.findtext("active", "").strip().casefold()
        if active not in {"0", "1", "false", "true"}:
            errors.append(f"Event {name}: invalid active flag {active!r}.")
        mode = event.findtext("limit", "").strip().casefold()
        if mode not in {"child", "parent", "mixed", "custom"}:
            errors.append(f"Event {name}: invalid limit mode {mode!r}.")
        position = event.findtext("position", "").strip().casefold()
        if position not in {"fixed", "player", "uniform"}:
            errors.append(f"Event {name}: invalid position mode {position!r}.")
        vehicle = name.casefold().startswith("vehicle")
        if vehicle and active in {"1", "true"} and (values.get("nominal") or 0) > 0:
            active_vehicles.append(event)
        if vehicle and all(values.get(k) is not None for k in ("min", "nominal", "max")):
            if not values["min"] <= values["nominal"] <= values["max"]:
                errors.append(f"Event {name}: expected min <= nominal <= max.")
        seen = set()
        for child in event.findall("children/child"):
            label = child.get("type", "").strip()
            if not label or label.casefold() in seen:
                errors.append(f"Event {name}: missing or duplicate child {label!r}.")
            seen.add(label.casefold())
            numbers = {}
            for field in ("min", "max", "lootmin", "lootmax"):
                try:
                    value = int(child.get(field, ""))
                    numbers[field] = value
                    if value < 0:
                        raise ValueError
                except ValueError:
                    errors.append(f"Event {name}, child {label}: {field} must be a nonnegative whole number.")
            if all(k in numbers for k in ("lootmin", "lootmax")) and numbers["lootmin"] > numbers["lootmax"]:
                errors.append(f"Event {name}, child {label}: lootmin exceeds lootmax.")
            # Custom infected min values are weights, not population minima.
            if vehicle and mode != "custom" and all(k in numbers for k in ("min", "max")) and numbers["min"] > numbers["max"]:
                errors.append(f"Event {name}, child {label}: min exceeds max.")

    positions = {}
    try:
        root = _xml(mission, mission / "cfgeventspawns.xml", "eventposdef")
        for event in root.findall("event"):
            name = event.get("name", "").strip()
            key = name.casefold()
            if not name or key in positions:
                errors.append(f"Vehicle positions: missing or duplicate event {name!r}.")
            valid = []
            for point in event.findall("pos"):
                try:
                    coordinates = {axis: float(point.get(axis, "")) for axis in ("x", "z")}
                    if any(not math.isfinite(n) or n < 0 for n in coordinates.values()):
                        raise ValueError
                    if any(not math.isfinite(float(point.get(axis, "0"))) for axis in ("a", "y")):
                        raise ValueError
                    valid.append((coordinates["x"], coordinates["z"]))
                except ValueError:
                    errors.append(f"Event positions {name}: invalid coordinate {dict(point.attrib)!r}.")
            if len(valid) != len(set(valid)):
                warnings.append(f"Event positions {name}: duplicate coordinates reduce distinct spawn sites.")
            positions[key] = valid
    except (OSError, ValueError, ET.ParseError) as error:
        errors.append(f"Cannot validate event positions: {error}")
    for event in active_vehicles:
        name = event.get("name", "")
        if event.findtext("position", "").strip().casefold() == "fixed" and not positions.get(name.casefold()):
            errors.append(f"Event {name}: active fixed vehicle event has no valid position.")

    chance_count = 0
    paths = [(p, "spawnabletypes") for p in refs["spawnabletypes"]]
    presets = mission / "cfgrandompresets.xml"
    if presets.exists() or presets.is_symlink():
        paths.append((presets, "randompresets"))
    for path, tag in paths:
        try:
            root = _xml(mission, path, tag)
            for parent in root:
                for node in parent.iter():
                    if "chance" not in node.attrib:
                        continue
                    chance_count += 1
                    try:
                        chance = float(node.get("chance", ""))
                        if not math.isfinite(chance) or not 0 <= chance <= 1:
                            raise ValueError
                    except ValueError:
                        errors.append(f"{path.relative_to(mission).as_posix()}, {parent.get('name', parent.tag)}: "
                                      f"invalid {node.tag} chance {node.get('chance')!r}; expected 0..1.")
        except (OSError, ValueError, ET.ParseError) as error:
            errors.append(f"Cannot validate probabilities in {path.name}: {error}")

    roots = {}
    try:
        core = _xml(mission, mission / "cfgeconomycore.xml", "economycore")
        if len(core.findall("classes")) != 1:
            errors.append("Economy core must contain one root-class section.")
        for node in core.findall("classes/rootclass"):
            name = node.get("name", "").strip()
            if not name or name.casefold() in roots:
                errors.append(f"Missing or duplicate economy root class {name!r}.")
            roots[name.casefold()] = node
        required = {"DefaultWeapon": None, "DefaultMagazine": None, "Inventory_Base": None,
                    "HouseNoDestruct": None, "SurvivorBase": "character", "DZ_LightAI": "character"}
        if active_vehicles:
            required["CarScript"] = "car"
        active_classes = {c.get("type", "").casefold() for event in active_vehicles for c in event.findall("children/child")}
        if any(name.startswith("motorbike_") for name in active_classes):
            required["MotorbikeScript"] = "car"
        if any(name.startswith("boat_01_") for name in active_classes):
            required["BoatScript"] = "car"
        # Additional-vehicle roots are declared in our audited exact-class catalog.
        catalog = APP_ROOT / "Custom/Transport/Workshop/catalog.json"
        if catalog.is_file():
            document = json.loads(catalog.read_text(encoding="utf-8-sig"))
            for pack in document.get("packs", []):
                for vehicle in pack.get("vehicles", []):
                    if vehicle.get("class", vehicle.get("name", "")).casefold() in active_classes:
                        required[vehicle["root"]] = "car"
        for name, act in required.items():
            node = roots.get(name.casefold())
            if node is None:
                errors.append(f"Missing required economy root class {name}.")
            elif act is not None and node.get("act") != act:
                errors.append(f"Economy root class {name} requires act={act}.")
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
        errors.append(f"Cannot validate economy root classes: {error}")
    return {"events": len(events), "active_vehicle_events": len(active_vehicles),
            "validated_probability_fields": chance_count, "root_classes": len(roots)}


def inspect_mission(mission: Path) -> dict:
    economy = read_economy(mission)
    errors = list(economy.warnings) + list(economy.contained_warnings)
    warnings = []
    sites = load_sites(mission)
    if sites is None:
        warnings.append('Static building placement could not be checked.')
    else:
        warnings.extend(sites.warnings)
    definitions = {}
    try:
        limits = parse_mission_xml((mission / 'cfglimitsdefinition.xml').read_bytes(), 'cfglimitsdefinition.xml')
        definitions = {kind: {n.get('name') for n in limits.findall(section + '/' + kind)}
                       for kind, section in (('category', 'categories'), ('tag', 'tags'),
                                             ('usage', 'usageflags'), ('value', 'valueflags'))}
    except (OSError, ValueError, ET.ParseError) as error:
        errors.append(f'Cannot read loot flag definitions: {error}')
    no_static_route = []
    for name in ('NailBox', 'CombinationLock', 'CombinationLock4', 'Ammo_545x39'):
        item = economy.types.get(name.casefold())
        if item is None or not item.natural:
            warnings.append(f'{name}: no positive ordinary loot target. Start Avalon with the updated '
                            'manager to apply the solo supply settings.')
    for item in economy.types.values():
        try:
            nominal, minimum = (_number(item.element, key) for key in ('nominal', 'min'))
            if nominal is not None and nominal < 0:
                errors.append(f'{item.name}: negative nominal.')
            # Event/scripted entities often have nominal0 with a positive min;
            # their event or script owns population. Compare ordinary loot only.
            if minimum is not None and (minimum < 0 or item.natural and nominal is not None and minimum > nominal):
                errors.append(f'{item.name}: min is negative or exceeds nominal.')
            lo, hi = (_number(item.element, key) for key in ('quantmin', 'quantmax'))
            if any(n is not None and not -1 <= n <= 100 for n in (lo, hi)) or (
                    lo is not None and hi is not None and lo >= 0 and hi >= 0 and lo > hi):
                errors.append(f'{item.name}: invalid quantity range {lo}..{hi}.')
            for key in ('lifetime', 'restock'):
                n = _number(item.element, key)
                if n is not None and n < 0:
                    errors.append(f'{item.name}: negative {key}.')
        except ValueError as error:
            errors.append(f'{item.name}: {error}')
        for kind, known in definitions.items():
            unknown = [n.get('name') for n in item.element.findall(kind)
                       if n.get('name') and n.get('name') not in known]
            if unknown:
                warnings.append(f'{item.name}: undefined {kind}: {", ".join(unknown)}.')
        if sites is not None and item.natural:
            categories = sites.flags(item.element, 'category')
            category = categories[0] if categories else None
            flags = item.element.find('flags')
            counted = flags is None or flags.get('count_in_map', '1') not in ('0', 'false')
            if counted and category != 'lootdispatch' and not sites.supports(
                    category=category, usages=sites.flags(item.element, 'usage'),
                    tags=sites.flags(item.element, 'tag')):
                no_static_route.append(item.name)
    if no_static_route:
        warnings.append('No static building route confirmed for: ' + ', '.join(no_static_route)
                        + '. Scripted/event placement may supply these; this is not proof they cannot spawn.')
    # Persistence switches are read separately from ordinary item targets.
    try:
        config = parse_mission_xml((mission / 'db/economy.xml').read_bytes(), 'db/economy.xml')
        for group in ('dynamic', 'vehicles', 'building', 'player'):
            node = config.find(group)
            if node is None or any(node.get(flag) != '1' for flag in ('load', 'save')):
                warnings.append(f'{group}: base economy load/save is not enabled; check any economy overrides.')
    except (OSError, ValueError, ET.ParseError) as error:
        errors.append(f'Cannot check economy persistence switches: {error}')
    event_checks = _check_event_inputs(mission.resolve(), errors, warnings)
    return {'mission': mission.name, 'loot_classes': len(economy.types),
            'event_checks': event_checks,
            'types_files': len(economy.type_files),
            'sources': dict(Counter(item.spawn_kind for item in economy.types.values())),
            'errors': errors, 'warnings': warnings,
            'no_static_route_confirmed': no_static_route}


def verify(root: Path, *, check_skin_assets: bool = True) -> dict:
    result = {'errors': [], 'warnings': [], 'missions': [], 'skins': None,
              'not_checked': [
                  'Native DayZ compilation, signature validation, joining and rendering.',
                  'Actual Workshop class definitions and mod-script behavior.',
                  'Vehicle wrap appearance, door matching and wear across native models.',
                  'Live loot occupancy, travel time, regional terrain tiers and spawn timing.']}
    if check_skin_assets:
        try:
            result['skins'] = check_sources(root)
            skin_settings.require_safe_unload(root, result['skins']['selected_ids'])
        except (OSError, ValueError, KeyError, TypeError) as error:
            result['errors'].append(f'Skin pack: {error}')
    world = root / 'Worlds/avalon'
    parent = world / 'World/Mission'
    candidates = sorted(p for p in parent.iterdir() if p.is_dir() and p.name.casefold().endswith('.avalon')) if parent.is_dir() else []
    if not candidates:
        result['warnings'].append('No staged Avalon mission found. Start Avalon once to prepare its installed files, '
                                  'stop it normally, then run this check again.')
    for mission in candidates:
        try:
            details = inspect_mission(mission)
            result['missions'].append(details)
            result['errors'].extend(f'{mission.name}: {line}' for line in details['errors'])
            result['warnings'].extend(f'{mission.name}: {line}' for line in details['warnings'])
        except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
            result['errors'].append(f'{mission.name}: {error}')
    if (world / 'Config/avalon-workshop.json').is_file():
        try:
            if avalon_workshop.version(world / 'Config') < avalon_workshop.PACK_VERSION:
                result['warnings'].append('The transport pack upgrade has not been prepared yet. '
                                          'Use Play Avalon to install its required Workshop mods and apply setup.')
            setup_report = world / 'Profiles/Azimuth/Avalon Loot Audit.txt'
            if setup_report.is_file():
                with setup_report.open('r', encoding='utf-8-sig') as stream:
                    recent_setup = stream.read(16 * 1024 * 1024)
                result['warnings'].extend('Latest setup report: ' + line for line in recent_setup.splitlines()
                                          if line.startswith('NEEDS SETUP:'))
        except (OSError, ValueError) as error:
            result['errors'].append(f'Cannot check transport setup state: {error}')
        fish = world / 'Profiles/Gebs/mpmissions'
        if not all((fish / filename).is_file() for filename in FISH_GENERATED.values()):
            result['warnings'].append('gebsfish generated economy files are absent. Its first successful server '
                                      'start creates them; the next start imports them. Vanilla fishing is separate.')
        for mission in candidates:
            if not (mission / 'azimuth_workshop' / FISH_ECONOMY_FOLDER / 'types.xml').is_file():
                result['warnings'].append(f'{mission.name}: gebsfish economy is not registered yet.')
            try:
                result['warnings'].extend(beehive_setup_notices(mission))
            except (OSError, ValueError, ET.ParseError) as error:
                result['errors'].append(f'{mission.name}: cannot check BeeHive setup: {error}')
    result['status'] = 'FAIL' if result['errors'] else 'REVIEW' if result['warnings'] else 'PASS'
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Print a machine-readable report.')
    args = parser.parse_args()
    result = verify(APP_ROOT)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print('AZIMUTH PLAYTHROUGH CHECK: ' + result['status'])
        print('Read-only configuration check. No settings, loot, saves or mods were changed.')
        for mission in result['missions']:
            print(f"{mission['mission']}: {mission['loot_classes']} loot classes in {mission['types_files']} type files")
        if result['skins']:
            print(f"Skins: {result['skins']['catalog_finishes']} catalog finishes checked.")
            print(f"Vehicle wraps: {'ON' if result['skins']['vehicle_wraps_enabled'] else 'OFF'}, "
                  f"{result['skins']['vehicle_wrap_chance_percent']}% configured chance on compatible vehicles.")
        for label, key in (('ERROR', 'errors'), ('REVIEW', 'warnings'), ('NOT TESTED', 'not_checked')):
            for line in result[key]:
                print(label + ': ' + line)
        print('Mapwide targets do not guarantee a specific item in the next building.')
    return 1 if result['errors'] else 2 if result['warnings'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
