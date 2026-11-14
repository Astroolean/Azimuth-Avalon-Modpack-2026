"""Import only installed, immutable, explicitly verified publisher CE rows.

The evidence manifest contains **names and hashes only**, never publisher assets.
Unknown or modified Workshop XML is not guessed or written to. The caller may
keep existing generated rows if the publisher's source changed during updates.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import stat
from xml.etree import ElementTree as ET

from azimuth_app.paths import APP_ROOT
from azimuth_app.core.backups import source_type
from azimuth_app.economy.avalon_loot_sites import LootSites

EVIDENCE = APP_ROOT / 'Custom/Economy/WorkshopEvidence/verified_publisher_economy.json'
CLASS_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*\Z')
MAX_XML = 2 * 1024 * 1024


def verified_rows(mod_id: str, installed_mod: Path) -> tuple[dict[str, ET.Element], tuple[str, ...]]:
    """Return verified casefolded class -> complete publisher XML row.

    Refuse unknown data, changed hashes, duplicate classes, XML with entities,
    and any class lacking a concrete declaration in the audited PBO inventory.
    """
    notes = []
    try:
        if not stat.S_ISREG(source_type(EVIDENCE)) or EVIDENCE.stat().st_size > 512 * 1024:
            raise ValueError('validated economy evidence manifest is missing or oversized')
        evidence = json.loads(EVIDENCE.read_text(encoding='utf-8'))
        if evidence.get('schema_version') != 1:
            raise ValueError('unsupported evidence schema')
        pack = evidence.get('packs', {}).get(mod_id)
        if not pack:
            return {}, ()  # A mod outside the audited, optional importer set
        if not stat.S_ISDIR(source_type(installed_mod)):
            raise ValueError('installed Workshop mod folder unavailable')
        allowed = pack['concrete_classes']
        allowed = {name.casefold() for name in allowed if isinstance(name, str) and CLASS_NAME.fullmatch(name)}
        if not allowed:
            raise ValueError('no verified concrete classes')
        # All-or-nothing per mod: no partial, misleading economy imports if
        # *any* publisher source differs from its inspected 2026 release.
        loaded = []
        for name, expected_hash in pack['files'].items():
            if (not isinstance(name, str) or '\\' in name or ':' in name or name.startswith('/')
                    or any(part in ('.', '..', '') for part in name.split('/'))):
                raise ValueError('unsafe publisher XML filename')
            path = installed_mod.joinpath(*name.split('/'))
            if not stat.S_ISREG(source_type(path)) or path.stat().st_size > MAX_XML:
                raise ValueError('missing or oversized publisher source: ' + name)
            if path.is_symlink() or not path.resolve().is_relative_to(installed_mod.resolve()):
                raise ValueError('publisher source crosses Workshop directory: ' + name)
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError('publisher XML updated since inspection: ' + name)
            if b'\0' in raw or any(mark in raw.upper() for mark in (b'<!DOCTYPE', b'<!ENTITY')):
                raise ValueError('unsupported publisher XML declarations: ' + name)
            root = ET.fromstring(raw)
            if root.tag != 'types':
                raise ValueError('publisher source is not a types.xml: ' + name)
            loaded.append((name, root))
        rows = {}
        for filename, root in loaded:
            for entry in root.findall('type'):
                classname = entry.get('name', '')
                if not CLASS_NAME.fullmatch(classname) or classname.casefold() in rows:
                    raise ValueError('unknown or duplicate class in publisher XML: ' + filename)
                if classname.casefold() not in allowed:
                    # Some XML examples intentionally include abstract entities;
                    # never expose them as live loot.
                    continue
                rows[classname.casefold()] = deepcopy(entry)
        return rows, ()
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError, json.JSONDecodeError) as error:
        return {}, (f'Workshop {mod_id} publisher economy not reimported: {error}. Prior verified loot is preserved when present.',)


def ordinary_target(entry: ET.Element) -> int:
    """A positive CE target is natural loot only if not crafted/event-only."""
    try:
        nominal = int(entry.findtext('nominal', '0'))
        minimum = int(entry.findtext('min', '0'))
        flags = entry.find('flags')
        flags = flags.attrib if flags is not None else {}
        if (nominal <= 0 or minimum < 0 or minimum > nominal
                or flags.get('crafted', '0').casefold() not in ('0', 'false')
                or flags.get('deloot', '0').casefold() not in ('0', 'false')):
            return 0
        return nominal
    except (TypeError, ValueError):
        return 0


def supported_on_map(entry: ET.Element, sites: LootSites | None) -> bool:
    """Require matching placed building and category/usage for ordinary loot."""
    if not sites or not ordinary_target(entry):
        return False
    category = next((p.get('name') for p in entry.findall('category') if p.get('name')), None)
    if not category:
        return False
    usages = sites.flags(entry, 'usage')
    values = sites.flags(entry, 'value')
    tags = sites.flags(entry, 'tag')
    return sites.supports(category=category, usages=usages, values=values, tags=tags)


def remembered_rows(mission: Path, slug: str, *, evidence_id: str) -> dict[str, ET.Element]:
    """Preserve previously integrated publisher rows if Workshop later updates.

    Only use allowlisted concrete names, only from Azimuth's own generated
    layer, never from arbitrary mission input or another Workshop package.
    """
    try:
        if not stat.S_ISREG(source_type(EVIDENCE)):
            return {}
        evidence = json.loads(EVIDENCE.read_text(encoding='utf-8'))
        allowed = {name.casefold() for name in evidence['packs'][evidence_id]['concrete_classes']}
        path = mission / 'azimuth_workshop' / slug / 'types.xml'
        if not stat.S_ISREG(source_type(path)) or path.stat().st_size > MAX_XML:
            return {}
        raw = path.read_bytes()
        if b'\0' in raw or any(mark in raw.upper() for mark in (b'<!DOCTYPE', b'<!ENTITY')):
            return {}
        root = ET.fromstring(raw)
        if root.tag != 'types':
            return {}
        result = {}
        for row in root.findall('type'):
            name = row.get('name', '').casefold()
            if name in allowed and name not in result:
                result[name] = deepcopy(row)
        return result
    except (OSError, ValueError, TypeError, KeyError, ET.ParseError):
        return {}


def correct_two_publisher_placement_gaps(entry: ET.Element, sites: LootSites | None) -> ET.Element | None:
    """Conservative Avalon-specific fixes for two inspected positive author kits.

    Only apply to the exact named publisher class after evidence hash and
    concrete-class checks; retain the item's authored population and policy.
    """
    if not sites or not ordinary_target(entry) or supported_on_map(entry, sites):
        return None
    name = entry.get('name', '').casefold()
    if name == 'rag_baseitems_military_locker_big_kit':
        # This publisher row is Industrial/Military, but its Tier4 value has
        # no matching placed Avalon group. Preserve both original usages.
        if (entry.find('category') is None or entry.find('category').get('name') != 'tools'
                or {x.get('name') for x in entry.findall('usage')} != {'Industrial', 'Military'}
                or [x.get('name') for x in entry.findall('value')] != ['Tier4']):
            return None
        adjusted = deepcopy(entry)
        for value in list(adjusted.findall('value')):
            if value.get('name') == 'Tier4':
                adjusted.remove(value)
    elif name == 'rag_baseitems_grammophone_kit':
        # Original author gives Office/School usages but no category, leaving
        # no inventory-container match. Its rare target stays at two.
        if (entry.find('category') is not None or entry.findall('value')
                or {x.get('name') for x in entry.findall('usage')} != {'Office', 'School'}):
            return None
        adjusted = deepcopy(entry)
        ET.SubElement(adjusted, 'category', name='tools')
    else:
        return None
    return adjusted if supported_on_map(adjusted, sites) else None


def correct_two_vehicle_spare_gaps(entry: ET.Element, sites: LootSites | None) -> ET.Element | None:
    """Make two verified publisher vehicle spares reachable at Avalon garages.

    Preserve author target population, restock and engine identity; replace
    only obsolete/missing category and usage metadata when known from source.
    """
    if not sites or not ordinary_target(entry) or supported_on_map(entry, sites):
        return None
    name = entry.get('name', '').casefold()
    if name == 'rag_gaz69_trunk':
        if (entry.find('category') is not None or entry.findall('value')
                or {n.get('name') for n in entry.findall('usage')} != {'Industrial'}):
            return None
        adjusted = deepcopy(entry)
        ET.SubElement(adjusted, 'category', name='tools')
    elif name == 'rag_schoolbus_hood':
        if (entry.find('category') is None or entry.find('category').get('name') != 'vehiclesparts'
                or {n.get('name') for n in entry.findall('usage')} != {'SchoolBus'}
                or entry.findall('value')):
            return None
        adjusted = deepcopy(entry)
        adjusted.find('category').set('name', 'tools')
        for usage in adjusted.findall('usage'):
            usage.set('name', 'Industrial')
        ET.SubElement(adjusted, 'usage', name='Farm')
    else:
        return None
    return adjusted if supported_on_map(adjusted, sites) else None
