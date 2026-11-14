"""Keep the 4K Kamaz's extra axle, seats and animations local to that truck."""
from __future__ import annotations
from collections.abc import Iterable
import json
import math
from pathlib import Path
import re
import stat
from azimuth_app.core.backups import source_type

DATA = Path("Custom/Transport/Workshop/vehicle_base_compat.json")
WORKSHOP_ID = "3387855369"
PATCH = "Azimuth_VehicleBaseIsolation"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,95}\Z")
PATHS = {( "Crew", f"Cargo{i}") for i in range(3,15)} | {( "AnimationSources", f"DoorsCargo{i}") for i in range(3,9)} | {
    ("AnimationSources", "AnimHitWheel_1_3"), ("AnimationSources", "AnimHitWheel_2_3"),
    ("SimulationModule", "Axles", "Middle")}


def _validate_node(node: object, *, depth: int = 0) -> None:
    if not isinstance(node, dict) or depth > 8:
        raise ValueError("Vehicle base isolation contains an invalid class tree.")
    for field in ("name", "parent"):
        value = node.get(field)
        if not isinstance(value, str) or (value and not IDENTIFIER.fullmatch(value)) or (field == "name" and not value):
            raise ValueError("Vehicle base isolation contains an invalid class name.")
    properties, classes = node.get("properties"), node.get("classes")
    if not isinstance(properties, dict) or not isinstance(classes, dict) or len(properties) > 64 or len(classes) > 32:
        raise ValueError("Vehicle base isolation contains an invalid class definition.")
    for key, value in properties.items():
        if not isinstance(key, str) or not IDENTIFIER.fullmatch(key):
            raise ValueError("Vehicle base isolation contains an invalid property.")
        if isinstance(value, str):
            if len(value) > 256 or any(ord(ch) < 32 for ch in value):
                raise ValueError("Vehicle base isolation contains an invalid string.")
        elif type(value) in (int, float):
            if not math.isfinite(value):
                raise ValueError("Vehicle base isolation contains a non-finite number.")
        else:
            raise ValueError("Vehicle base isolation permits only the recorded scalar properties.")
    for name, child in classes.items():
        _validate_node(child, depth=depth+1)
        if child["name"] != name:
            raise ValueError("Vehicle base isolation contains a mismatched child class.")


def _load(root: Path) -> dict:
    path = root / DATA
    if not stat.S_ISREG(source_type(path)):
        raise ValueError("Vehicle base isolation data must be an ordinary file.")
    with path.open("rb") as stream:
        raw = stream.read(128 * 1024 + 1)
    if len(raw) > 128 * 1024:
        raise ValueError("Vehicle base isolation data exceeds its size limit.")
    data = json.loads(raw.decode("utf-8-sig"))
    expected = {"schema_version": 1, "workshop_id": WORKSHOP_ID, "shared_class": "CarScript", "shared_parent": "Car", "truck_class": "Kamaz_TyphoonK", "truck_parent": "CarScript"}
    if not isinstance(data, dict) or any(data.get(key) != value for key, value in expected.items()):
        raise ValueError("Vehicle base isolation metadata is invalid.")
    deps = data.get("required_addons")
    if not isinstance(deps, list) or not 1 <= len(deps) <= 32 or any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in deps) or "Kamaz_TyphoonK" not in deps:
        raise ValueError("Vehicle base isolation requires its exact publisher addons.")
    rows = data.get("isolation")
    if not isinstance(rows, list) or len(rows) != len(PATHS):
        raise ValueError("Vehicle base isolation must contain the recorded Kamaz-only paths.")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), list):
            raise ValueError("Vehicle base isolation has an invalid path.")
        path_tuple = tuple(row["path"])
        if path_tuple not in PATHS or path_tuple in seen:
            raise ValueError("Vehicle base isolation must not change unrelated classes.")
        seen.add(path_tuple)
        definition = row.get("definition")
        _validate_node(definition)
        expected_parent = "AnimHitWheel_1_1" if path_tuple[-1] in {"AnimHitWheel_1_3", "AnimHitWheel_2_3"} else ""
        if definition["name"] != path_tuple[-1] or definition["parent"] != expected_parent:
            raise ValueError("Isolated Kamaz children must be independent of the removed shared child.")
    return data


def _render_node(node: dict, level: int) -> list[str]:
    pad = "    " * level
    parent = ": " + node["parent"] if node["parent"] else ""
    lines = [f"{pad}class {node['name']}{parent}", pad + "{"]
    for key, value in node["properties"].items():
        lines.append(f"{pad}    {key} = {json.dumps(value, ensure_ascii=True, allow_nan=False)};")
    for child in node["classes"].values():
        lines.extend(_render_node(child, level+1))
    lines.append(pad + "};")
    return lines


def config_parts(root: Path, active_workshop_ids: Iterable[str]) -> tuple[str, str]:
    """Return inner CfgPatches and CfgVehicles text for the active 4K pack only."""
    if WORKSHOP_ID not in set(active_workshop_ids):
        return "", ""
    data = _load(root)
    from azimuth_app.transport.vehicle_scalar_compat import config_parts as scalar_parts
    scalar_patch, car_fields, scalar_classes = scalar_parts(root)
    deps = ", ".join(json.dumps(x) for x in sorted(set(data["required_addons"]), key=str.lower))
    patch = f'''    class {PATCH}
    {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{{deps}}};
    }};
'''
    # Restore audited native scalar defaults separately from axle/seat isolation.
    # The truck gets independent definitions, so it never derives from a deleted child.
    lines = ["    // Publisher truck-only definitions must not become defaults for other cars.", "    class Car;", "    class CarScript: Car", "    {"]
    lines.append(car_fields)
    for group in ("Crew", "AnimationSources", "SimulationModule"):
        rows = [r for r in data["isolation"] if r["path"][0] == group]
        lines.extend((f"        class {group}", "        {"))
        if group == "SimulationModule":
            lines.extend(("            class Axles", "            {", "                delete Middle;", "            };"))
        else:
            lines.extend(f"            delete {row['path'][-1]};" for row in rows)
        lines.append("        };")
    lines.extend(("    };", "    class Kamaz_TyphoonK: CarScript", "    {"))
    for group in ("Crew", "AnimationSources", "SimulationModule"):
        rows = [r for r in data["isolation"] if r["path"][0] == group]
        lines.extend((f"        class {group}: {group}", "        {"))
        if group == "SimulationModule":
            lines.extend(("            class Axles: Axles", "            {"))
            lines.extend(_render_node(rows[0]["definition"],4))
            lines.append("            };")
        else:
            if group == "AnimationSources":
                # The rear wheel animations derive from this native CarScript node.
                # Defining its verified native fields here makes it available in
                # Kamaz's new nested scope without modifying other vehicle classes.
                lines.extend((
                    "            class AnimHitWheel_1_1",
                    "            {",
                    '                source = "Hit";',
                    '                hitpoint = "HitWheel_1_1";',
                    "                raw = 1;",
                    "            };",
                ))
            for row in rows:
                lines.extend(_render_node(row["definition"],3))
        lines.append("        };")
    lines.append("    };")
    return patch + scalar_patch, "\n".join(lines)+"\n" + scalar_classes
