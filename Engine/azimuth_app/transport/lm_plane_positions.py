"""Allocate fixed-wing aircraft at existing Avalon airfield event anchors.

Positions retain the map author's coordinates and heading. This is static map
evidence, not a claim of native terrain, takeoff or flight testing. The largest
decoded land-plane model is the LM DC-3 (29.01 m span, 19.91 m length); the
200 m mapped-object-origin margin is deliberately larger than its bounds.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from xml.etree import ElementTree as ET

from azimuth_app.economy.hunter_economy import _read_xml
from azimuth_app.transport.avalon_air_transport import _coordinates

SOURCE_EVENT = 'StaticHeliCrash'
EVENT = 'VehicleAzimuthWorkshopPlane'
HANGAR_PREFIXES = ('land_hangar_avalon', 'land_airport_small_hangar')
MAX_HANGAR_DISTANCE = 500.0
MIN_OBJECT_ORIGIN_DISTANCE = 200.0
MIN_OTHER_EVENT_DISTANCE = 100.0
MAX_POSITIONS = 2


def positions(mission: Path, root: ET.Element) -> list[ET.Element]:
    """Mutate an in-memory positions tree; the caller commits transactionally.

    An-2 uses a different, closer airfield point. A selected crash anchor is
    transferred, so a plane and wreck cannot share it. Repeated preparations
    retain the same managed anchors rather than consuming more crash sites.
    """
    groups = _read_xml(mission / 'mapgrouppos.xml', 'map', preserve_comments=True)
    objects, hangars = [], []
    for group in groups.findall('group'):
        try:
            values = tuple(float(n) for n in group.get('pos', '').split())
        except ValueError:
            continue
        if len(values) != 3 or not all(math.isfinite(v) for v in values):
            continue
        point = values[0], values[2]
        objects.append(point)
        if group.get('name', '').casefold().startswith(HANGAR_PREFIXES):
            hangars.append(point)
    if not objects or not hangars:
        raise ValueError('No authored Avalon airfield hangars are available for fixed-wing planes.')
    existing = [e for e in root.findall('event') if e.get('name', '').casefold() == EVENT.casefold()]
    sources = [e for e in root.findall('event') if e.get('name', '').casefold() == SOURCE_EVENT.casefold()]
    if len(existing) > 1 or len(sources) != 1:
        raise ValueError('Fixed-wing plane placement needs unique managed and source events.')
    other = [point for event in root.findall('event')
             if event not in existing + sources
             for node in event.findall('pos') if (point := _coordinates(node)) is not None]

    def accepted(node: ET.Element) -> tuple[float, float] | None:
        point = _coordinates(node)
        if point is None:
            return None
        x, z = point
        if min(math.hypot(x - xx, z - zz) for xx, zz in hangars) > MAX_HANGAR_DISTANCE:
            return None
        if min(math.hypot(x - xx, z - zz) for xx, zz in objects) < MIN_OBJECT_ORIGIN_DISTANCE:
            return None
        if any(math.hypot(x - xx, z - zz) < MIN_OTHER_EVENT_DISTANCE for xx, zz in other):
            return None
        return point

    if existing:
        result = existing[0].findall('pos')
        points = [accepted(node) for node in result]
        if not result or len(result) > MAX_POSITIONS or None in points or len(set(points)) != len(points):
            raise ValueError('Existing fixed-wing plane anchors failed static validation; their positions were preserved.')
        if any(_coordinates(node) in points for node in sources[0].findall('pos')):
            raise ValueError('A fixed-wing plane anchor is also allocated to a crash event; existing positions were preserved.')
        return deepcopy(result)
    candidates = sorted(((point, node) for node in sources[0].findall('pos')
                         if (point := accepted(node)) is not None), key=lambda item: item[0])
    selected, seen = [], set()
    for point, node in candidates:
        if point in seen:
            raise ValueError('The authored plane candidate has duplicate crash-event coordinates.')
        seen.add(point)
        if all(math.hypot(point[0] - x, point[1] - z) >= MIN_OTHER_EVENT_DISTANCE
               for x, z in (_coordinates(n) for n in selected)):
            selected.append(node)
    selected = selected[:MAX_POSITIONS]
    if not selected:
        raise ValueError('No authored airfield event anchors meet fixed-wing plane static placement checks.')
    event = ET.SubElement(root, 'event', name=EVENT)
    for node in selected:
        sources[0].remove(node)
        event.append(deepcopy(node))
    return deepcopy(selected)
