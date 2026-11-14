"""Bounded automatic native-terrain survey; never creates or removes a vehicle.

Candidates come from this mission's own road/water sites. The server validates
water depth or level ground across a conservative footprint and collision volume.
Only a complete matching report can seed new CE sites on the following start.
"""
from __future__ import annotations
from hashlib import sha256
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET
from azimuth_app.paths import APP_ROOT
from azimuth_app.core.backups import source_type
from azimuth_app.core.worlds import atomic_text
from azimuth_app.cosmetics.native_color_build import build_script_pbo
from azimuth_app.transport.avalon_air_transport import _anchors, _helipads, _spread
from azimuth_app.economy.hunter_economy import _read_xml

ALGORITHM = 1
REQUEST = 'Transport_Placement_Request.json'
REPORT = 'Transport_Placement_Survey.json'
LIMIT = 2048
ROUTES = {'large_water': (100.0, 100.0, 12.0), 'airship': (40.0, 80.0, 0.0), 'air': (30.0, 50.0, 0.0)}
EVENTS = {'large_water': 'VehicleAzimuthWorkshopLargeWater', 'airship': 'VehicleAzimuthWorkshopAirship', 'air': 'VehicleAzimuthWorkshopAir'}

def _read(path: Path, *, transient: bool = False):
    if not path.exists(): return None
    source_type(path)
    if path.stat().st_size > 1024 * 1024: raise ValueError('Transport survey file exceeds its size limit.')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        # The native writer may be between opening and completing its report.
        # Treat that moment as pending, never as usable placement or a launch error.
        if transient: return None
        raise
    if not isinstance(value, dict): raise ValueError('Transport survey must be an object.')
    return value

def _number(v):
    return type(v) in (float, int) and math.isfinite(v)

def _request(mission: Path) -> dict:
    root = _read_xml(mission / 'cfgeventspawns.xml', 'eventposdef')
    road = _spread(_anchors(root, ('VehicleOffroadHatchback', 'VehicleOffroad02', 'VehicleSedan02')), 16)
    water = _spread(_anchors(root, ('VehicleBoat',)), 16)
    try: pads, _ = _helipads(mission)
    except (OSError, ValueError): pads = []
    excluded = [{'x': float(p.get('x')), 'z': float(p.get('z'))} for p in pads]
    candidates = []
    seen = set()
    # Repeat radii over every real anchor, not invented global map coordinates.
    for route, anchors, radii in (('large_water', water, (250, 450, 700, 1000)),
                                  ('airship', pads + road, (200, 400, 650)),
                                  ('air', pads + road, (200, 400, 650))):
        for radius in radii:
            for p in anchors:
                for angle in range(0, 360, 45):
                    r = math.radians(angle)
                    x = round(float(p.get('x')) + math.sin(r) * radius, 3)
                    z = round(float(p.get('z')) + math.cos(r) * radius, 3)
                    key = route, x, z
                    if min(x,z) <= 150 or max(x,z) >= 100000 or key in seen: continue
                    seen.add(key)
                    candidates.append({'route': route, 'x': x, 'z': z, 'a': angle})
    request = {'schema_version': 1, 'algorithm_version': ALGORITHM, 'world_name': 'Avalon',
               'candidates': candidates[:LIMIT], 'excluded': excluded}
    request['request_id'] = sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return request

def prepare_survey(mission: Path, profiles: Path, config: Path) -> Path:
    folder = profiles / 'Azimuth'; folder.mkdir(parents=True, exist_ok=True); source_type(folder)
    request = _request(mission)
    path = folder / REQUEST
    if _read(path) != request: atomic_text(path,json.dumps(request,indent=2)+'\n')
    mod = config / 'TransportSurvey' / '@AzimuthTransportSurvey'
    build_script_pbo(APP_ROOT/'WorldSetup/avalon/AzimuthTransportSurvey',mod,'azimuthtransportsurvey')
    return mod

def _matching(mission: Path, profiles: Path):
    request = _read(profiles/'Azimuth'/REQUEST)
    if not request or request != _request(mission): return None, None
    report = _read(profiles/'Azimuth'/REPORT, transient=True)
    if not report or any(report.get(k) != request.get(k) for k in ('schema_version','algorithm_version','request_id','world_name')): return request, None
    return request, report

def validated_sites(mission: Path, profiles: Path) -> dict:
    empty = {route: [] for route in ROUTES}
    request, report = _matching(mission, profiles)
    if not report or report.get('status') != 'complete': return empty
    size = report.get('world_size')
    if not _number(size) or not 1000 <= size <= 100000: raise ValueError('Invalid surveyed world size.')
    candidates = request['candidates']
    selected = []
    for route,(radius,height,depth) in ROUTES.items():
        rows=report.get(route)
        if not isinstance(rows,list) or len(rows)>4: raise ValueError('Invalid transport survey route list.')
        for p in rows:
            if not isinstance(p,dict) or any(not _number(p.get(k)) for k in ('x','y','z','a','footprint_radius_m','min_depth_m','max_height_delta_m','obstacle_clearance_m')):
                raise ValueError('Invalid transport survey point.')
            x,y,z,a=(p[k] for k in ('x','y','z','a'))
            # DayZ serializes float32 coordinates; accept rounding, not a new site.
            requested = any(c['route']==route and abs(c['x']-x)<=0.02 and abs(c['z']-z)<=0.02 and abs(c['a']-a)<=0.001 for c in candidates)
            if not requested or not radius < min(x,z) or max(x,z)>=size-radius or abs(y)>10000:
                raise ValueError('Transport survey point does not match this request or world.')
            if p.get('sample_count') != 81 or p['footprint_radius_m']<radius or p['obstacle_clearance_m']<height:
                raise ValueError('Transport survey footprint/clearance is incomplete.')
            if route=='large_water':
                if p.get('surface')!='sea' or p['min_depth_m']<depth: raise ValueError('Large vessel site is too shallow.')
            elif p.get('surface')!='land' or p['max_height_delta_m']>0.5 or p['max_height_delta_m']<0:
                raise ValueError('Aircraft site is not sufficiently level.')
            if any(math.hypot(x-o['x'],z-o['z'])<350 for o in [*selected,*request['excluded']]):
                raise ValueError('Surveyed vehicle sites overlap.')
            selected.append(p)
            empty[route].append(ET.Element('pos',x=str(x),y=str(y),z=str(z),a=str(a)))
    return empty

def setup_pending(mission: Path, profiles: Path) -> bool:
    sites=validated_sites(mission,profiles)
    root=_read_xml(mission/'cfgeventspawns.xml','eventposdef')
    for route,points in sites.items():
        existing = _anchors(root,(EVENTS[route],))
        if points and not existing: return True
        if route == 'air' and points:
            old = {(round(float(p.get('x')),3),round(float(p.get('z')),3)) for p in existing}
            if any((round(float(p.get('x')),3),round(float(p.get('z')),3)) not in old for p in points): return True
    return False

def survey_running(mission: Path, profiles: Path) -> bool:
    request,report=_matching(mission,profiles)
    return bool(request and (not report or report.get('status') not in ('complete','failed')))
