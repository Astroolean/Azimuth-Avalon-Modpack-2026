"""Keep recorded publisher scalar defaults local and restore native shared bases."""
from __future__ import annotations

from collections import OrderedDict
import json
import math
from pathlib import Path
import re
import stat

from azimuth_app.core.backups import source_type

DATA = Path('Custom/Transport/Workshop/vehicle_scalar_compat.json')
WORKSHOP_ID = '3387855369'
PATCH = 'Azimuth_VehicleScalarIsolation'
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,95}\Z')
FIELDS = {
    'CarScript': {('fuelCapacity',): 50},
    'CarWheel': {('weight',): 12000, ('DamageSystem', 'GlobalHealth', 'Health', 'hitpoints'): 200},
    'CarDoor': {('DamageSystem', 'GlobalHealth', 'Health', 'hitpoints'): 500},
}
PARENTS = {'CarScript': 'Car', 'CarWheel': 'Inventory_Base', 'CarDoor': 'Inventory_Base'}


def _load(root: Path) -> dict:
    path = root / DATA
    if not stat.S_ISREG(source_type(path)):
        raise ValueError('Vehicle scalar isolation needs an ordinary data file.')
    with path.open('rb') as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError('Vehicle scalar isolation data exceeds its limit.')
    data = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(data, dict) or data.get('schema_version') != 1 or data.get('workshop_id') != WORKSHOP_ID:
        raise ValueError('Vehicle scalar isolation metadata is invalid.')
    deps = data.get('required_addons')
    if not isinstance(deps, list) or not 1 <= len(deps) <= 64 or any(not isinstance(n, str) or not IDENTIFIER.fullmatch(n) for n in deps):
        raise ValueError('Vehicle scalar isolation requires exact publisher addons.')
    for key in ('shared_defaults', 'publisher_local_defaults'):
        rows = data.get(key)
        if not isinstance(rows, list) or len(rows) > 512:
            raise ValueError('Vehicle scalar isolation has invalid rows.')
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('class'), str) or not IDENTIFIER.fullmatch(row['class']):
                raise ValueError('Vehicle scalar isolation has an invalid class.')
            path_parts = row.get('path')
            if not isinstance(path_parts, list) or any(not isinstance(p, str) for p in path_parts):
                raise ValueError('Vehicle scalar isolation has an invalid property path.')
            path_tuple = tuple(path_parts)
            base = row['class'] if key == 'shared_defaults' else row.get('parent')
            if base not in FIELDS or path_tuple not in FIELDS[base]:
                raise ValueError('Vehicle scalar isolation cannot change unrelated properties.')
            if key == 'shared_defaults':
                if row.get('parent') != PARENTS[base] or row.get('value') != FIELDS[base][path_tuple]:
                    raise ValueError('Vehicle scalar isolation must retain the recorded native default.')
            elif row['class'] in PARENTS:
                raise ValueError('Publisher defaults must be local to publisher subclasses.')
            val = row.get('value')
            if type(val) not in (int, float) or not math.isfinite(val) or val <= 0:
                raise ValueError('Vehicle scalar isolation has an invalid value.')
            identity = (row['class'].lower(), path_tuple)
            if identity in seen:
                raise ValueError('Vehicle scalar isolation repeats a property.')
            seen.add(identity)
        if key == 'shared_defaults' and seen != {(n.lower(), p) for n, fields in FIELDS.items() for p in fields}:
            raise ValueError('Vehicle scalar isolation must restore every recorded native field.')
    return data


def _class_rows(rows: list[dict]) -> list[str]:
    classes = OrderedDict()
    for row in rows:
        name = row['class']
        if name not in classes:
            classes[name] = (row['parent'], {})
        parent, tree = classes[name]
        if parent != row['parent']:
            raise ValueError('Vehicle scalar isolation repeats a class with different parents.')
        node = tree
        for part in row['path'][:-1]:
            node = node.setdefault(part, {})
        node[row['path'][-1]] = row['value']
    lines = []
    def render(tree, depth, inherit):
        pad = '    ' * depth
        for name, value in tree.items():
            if isinstance(value, dict):
                lines.extend((f'{pad}class {name}' + (f': {name}' if inherit else ''), pad + '{'))
                render(value, depth + 1, inherit)
                lines.append(pad + '};')
            else:
                lines.append(f'{pad}{name} = {json.dumps(value, allow_nan=False)};')
    for name, (parent, tree) in classes.items():
        lines.extend((f'    class {name}: {parent}', '    {'))
        render(tree, 2, name not in PARENTS)
        lines.append('    };')
    return lines


def config_parts(root: Path) -> tuple[str, str, str]:
    """Return patch, CarScript fields, and other CfgVehicles class definitions."""
    data = _load(root)
    deps = ', '.join(json.dumps(n) for n in sorted(set(data['required_addons']), key=str.lower))
    patch = f'''    class {PATCH}
    {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{{deps}}};
    }};
'''
    # CarScript is already reopened by the axle isolation in this same config.
    # Add its scalar there rather than emitting a duplicate class definition.
    car_fields = '\n'.join(f"        {r['path'][0]} = {r['value']};" for r in data['shared_defaults'] if r['class'] == 'CarScript')
    other = [r for r in data['shared_defaults'] if r['class'] != 'CarScript'] + data['publisher_local_defaults']
    classes = '    class Inventory_Base;\n' + '\n'.join(_class_rows(other)) + '\n'
    return patch, car_fields, classes
