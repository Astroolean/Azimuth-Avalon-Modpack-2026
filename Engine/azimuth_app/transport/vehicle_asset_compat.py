"""Repair confirmed publisher rendering references without changing damage.

Use same-vehicle aliases where supplied. Unavailable window damage materials
become empty material stages, retaining the model material rather than requesting
a nonexistent asset. Health thresholds and terminal window hiding are preserved.
"""
from __future__ import annotations
import json

WORKSHOP_ID = '3411055412'
PATCH = 'Azimuth_VehicleAssetCompatibility'
# The installed Van_Cybertruck PBO contains all three glass*.rvmat states, but
# its four window zones reference nonexistent steklo*.rvmat files. Keep every
# authored threshold and the special 'hidden' terminal state unchanged.
STATES = ('glass.rvmat', 'glass_damage.rvmat', 'glass_destruct.rvmat')
SOURCE_SHA256 = 'ad2c10d93505b64f6a08ef3b166969411f6d72801655c0b372b7a2483558e3d3'
SOURCE_HASHES = {
    'Van_Cybertruck': SOURCE_SHA256,
    'Van_CamperRV': '18b277bd5ba0aa3f430c19ddf6e6f0ef34b22b71d0e607365a649bf6618b3191',
    'Van_DodgePhoenix': '8f0b89f9b3dbd2b53036372ff498be2e3e0c7fea87210bf7ac5ab3a826f5acfc',
    'Van_HiluxCamper': 'f1e5facc3a6e2ef289758e16a5dc4ac44dfd37ccbd674ef1d1c510826eb7116e',
}

# These exterior materials are present in the supplied door models and in the
# corresponding publisher PBOs. The author's armor_body paths exist in neither.
DOOR_MATERIAL_SELECTORS = {
    'Van_CamperRV_Doors_Driver': ['van_camperrv/data/car_paint.rvmat'],
    'Van_HiluxCamper_Doors_Driver': [
        'van_hiluxcamper/data/van_hiluxcamper_body.rvmat',
        'van_hiluxcamper/data/van_hiluxcamper_parts.rvmat',
    ],
}


def _value(value):
    if isinstance(value, list):
        return '{' + ', '.join(_value(item) for item in value) + '}'
    if isinstance(value, str):
        value = value.replace('\\', '/')
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def _window_levels(front: bool, material_folder: str | None = None) -> list:
    """Copy the publisher's exact thresholds; only material arrays may change."""
    materials = [[material_folder + '/' + state] for state in STATES] if material_folder else [[], [], []]
    return [
        [1.0099999904632568 if front else 1, materials[0]],
        [0.699999988079071, []],
        [0.5, materials[1]],
        [0.30000001192092896, materials[2]],
        [0.009999999776482582 if front else 0, 'hidden'],
    ]


def _append_zones(lines: list[str], name: str, parent: str, zones: dict) -> None:
    """Patch vehicle window materials without adding unresolved nested bases."""
    lines.extend((f'    class {name}: {parent}', '    {',
                  '        class DamageSystem', '        {',
                  '            class DamageZones', '            {'))
    for zone, properties in zones.items():
        lines.extend((f'                class {zone}', '                {',
                      '                    class Health', '                    {'))
        for key, value in properties.items():
            lines.append(f'                        {key}[] = {_value(value)};')
        lines.extend(('                    };', '                };'))
    lines.extend(('            };', '        };', '    };'))


def _camper_door_levels() -> list:
    """Exact door health thresholds and vanilla door materials from the publisher."""
    folder = 'dz/vehicles/wheeled/offroadhatchback/data/green'
    return [
        [1.0099999904632568, [folder + '/niva_door.rvmat']],
        [0.699999988079071, [folder + '/niva_door.rvmat']],
        [0.5, [folder + '/niva_door_damage.rvmat']],
        [0.30000001192092896, [folder + '/niva_door_damage.rvmat']],
        [0.009999999776482582, [folder + '/niva_door_destruct.rvmat']],
    ]


def _native_car_door_global_levels() -> list:
    """Captured DayZ CarDoor GlobalHealth values, required when opening anew."""
    return [
        [1, []], [0.699999988079071, []], [0.5, []],
        [0.30000001192092896, []], [0, []],
    ]


def _append_complete_camper_door(lines: list[str], name: str, selectors: list[str]) -> None:
    """Use an explicit complete damage tree, not fragile nested : SameName refs.

    The original camper addons inherit CarDoor/DamageSystem/DamageZones/Doors/
    Window/Health at multiple levels. Reopening a previously supplied class in
    another addon does not reliably resolve those nested bases at DayZ startup.
    Emit the publisher's complete captured door-damage values instead. The only
    changes are two nonexistent armor material paths and the Hilux's unavailable
    window material states; no hitpoints, coefficients or thresholds change.
    """
    lines.extend((f'    class {name}: CarDoor', '    {',
                  '        class DamageSystem', '        {',
                  '            class GlobalHealth', '            {',
                  '                class Health', '                {',
                  '                    hitpoints = 500;',
                  '                    healthLevels[] = ' + _value(_native_car_door_global_levels()) + ';',
                  '                };', '            };',
                  '            class DamageZones', '            {'))
    windows = (_window_levels(True, 'Van_CamperRV/data')
               if name == 'Van_CamperRV_Doors_Driver' else _window_levels(True))
    for zone, health in (
        ('Doors', {
            'hitpoints': 52000,
            'transferToGlobalCoef': 0.5,
            'RefTexsMats': selectors,
            'healthLevels': _camper_door_levels(),
        }),
        ('Window', {
            'hitpoints': 52000,
            'transferToGlobalCoef': 0.5,
            'healthLevels': windows,
        }),
    ):
        lines.extend((f'                class {zone}', '                {',
                      '                    componentNames[] = ' + _value(['dmgZone_' + zone.lower()]) + ';',
                      '                    fatalInjuryCoef = -1;',
                      '                    inventorySlots[] = {};',
                      '                    class Health', '                    {'))
        for key, value in health.items():
            suffix = '[]' if isinstance(value, list) else ''
            lines.append(f'                        {key}{suffix} = {_value(value)};')
        lines.extend(('                    };', '                };'))
    lines.extend(('            };', '        };', '    };'))


def config_parts(active_workshop_ids) -> tuple[str, str]:
    if WORKSHOP_ID not in set(active_workshop_ids):
        return '', ''
    patch = f'''    class {PATCH}
    {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{"Van_Cybertruck", "Van_CamperRV", "Van_DodgePhoenix", "Van_HiluxCamper"}};
    }};
'''
    lines = ['    class CarScript;', '    class CarDoor;']
    for vehicle in ('Van_Cybertruck', 'Van_DodgePhoenix', 'Van_HiluxCamper'):
        folder = 'Van_Cybertruck/data' if vehicle == 'Van_Cybertruck' else None
        zones = {name: {'healthLevels': _window_levels(name == 'Windowfront', folder)}
                 for name in ('Windowfront', 'Windowrear', 'Windowleft', 'Windowright')}
        _append_zones(lines, vehicle, 'CarScript', zones)
    for vehicle, selectors in DOOR_MATERIAL_SELECTORS.items():
        _append_complete_camper_door(lines, vehicle, selectors)
    return patch, '\n'.join(lines) + '\n'
