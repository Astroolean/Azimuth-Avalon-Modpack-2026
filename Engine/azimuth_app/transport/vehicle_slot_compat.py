"""Preserve cargo slots shared by active vehicle publishers without replacing arrays."""
from __future__ import annotations
from collections.abc import Iterable
import json
from pathlib import Path
import re
import stat
from azimuth_app.core.backups import source_type

DATA = Path("Custom/Transport/Workshop/slot_compat.json")
PATCH = "Azimuth_VehicleSlotCompatibility"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
SLOT = re.compile(r"[A-Za-z0-9_]{1,96}\Z")
WORKSHOP_ID = re.compile(r"[0-9]{6,20}\Z")
MAX_DATA_BYTES = 128 * 1024
PARENTS = {"CanisterGasoline": "Bottle_Base", "Barrel_ColorBase": "Container_Base"}


def _names(value: object, *, slots: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("Vehicle slot compatibility contains an invalid name list.")
    if any(not isinstance(name, str) or not (SLOT if slots else IDENTIFIER).fullmatch(name) for name in value):
        raise ValueError("Vehicle slot compatibility contains an invalid config name.")
    return list(value)


def _load(root: Path) -> list[dict]:
    path = root / DATA
    if not stat.S_ISREG(source_type(path)):
        raise ValueError("Vehicle slot compatibility data must be an ordinary file.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_DATA_BYTES + 1)
    if len(raw) > MAX_DATA_BYTES:
        raise ValueError("Vehicle slot compatibility data exceeds its size limit.")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Vehicle slot compatibility data has an unsupported version.")
    classes = data.get("classes")
    if not isinstance(classes, list) or len(classes) != len(PARENTS):
        raise ValueError("Vehicle slot compatibility must contain the two supported cargo classes.")
    seen = set()
    for row in classes:
        if not isinstance(row, dict) or row.get("name") not in PARENTS:
            raise ValueError("Vehicle slot compatibility contains an unsupported class.")
        if row["name"] in seen or row.get("parent") != PARENTS[row["name"]]:
            raise ValueError("Vehicle slot compatibility must retain the native class parents.")
        seen.add(row["name"])
        providers = row.get("providers")
        if not isinstance(providers, list) or len(providers) > 32:
            raise ValueError("Vehicle slot compatibility has an invalid publisher list.")
        ids = set()
        for provider in providers:
            if not isinstance(provider, dict):
                raise ValueError("Vehicle slot compatibility has an invalid publisher.")
            wid = provider.get("workshop_id")
            if not isinstance(wid, str) or not WORKSHOP_ID.fullmatch(wid) or wid in ids:
                raise ValueError("Vehicle slot compatibility has an invalid Workshop ID.")
            ids.add(wid)
            if not _names(provider.get("required_addons")):
                raise ValueError("Vehicle slot compatibility needs exact publisher addon dependencies.")
            _names(provider.get("slots"), slots=True)
            replacements = provider.get("replacements")
            if not isinstance(replacements, list) or len(replacements) > 64:
                raise ValueError("Vehicle slot compatibility has invalid replacement evidence.")
            for replacement in replacements:
                _names(replacement, slots=True)
    return classes


def _render_slot_parts(root: Path, active_workshop_ids: Iterable[str]) -> tuple[str, str]:
    active = set()
    for value in active_workshop_ids:
        if not isinstance(value, str) or not WORKSHOP_ID.fullmatch(value):
            raise ValueError("Active vehicle Workshop IDs must be numeric strings.")
        active.add(value)
    if not active:
        return "", ""
    dependencies: dict[str, str] = {}
    repairs = []
    for row in _load(root):
        providers = [p for p in row["providers"] if p["workshop_id"] in active]
        replacements = [values for p in providers for values in p["replacements"]]
        if not replacements:
            continue
        # Slots present in every replacing definition are already guaranteed.
        guaranteed = set.intersection(*(set(name.lower() for name in values) for values in replacements))
        needed: dict[str, str] = {}
        for provider in providers:
            for name in provider["slots"]:
                if name.lower() not in guaranteed:
                    needed.setdefault(name.lower(), name)
        if not needed:
            continue
        for provider in providers:
            for name in provider["required_addons"]:
                dependencies.setdefault(name.lower(), name)
        repairs.append((row["name"], row["parent"], sorted(needed.values(), key=str.lower)))
    if not repairs:
        return "", ""
    required = ", ".join(json.dumps(name) for name in sorted(dependencies.values(), key=str.lower))
    patch = f'''    class {PATCH}
    {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{{required}}};
    }};
'''
    lines = ["    // Restore only active publishers' cargo slots after their declared replacements."]
    for parent in sorted({parent for _, parent, _ in repairs}):
        lines.append(f"    class {parent};")
    for name, parent, slots in repairs:
        values = ", ".join(json.dumps(slot) for slot in slots)
        lines.extend((f"    class {name}: {parent}", "    {", f"        inventorySlot[] += {{{values}}};", "    };"))
    return patch, "\n".join(lines) + "\n"


# A late forward declaration is not harmless in DayZ config.cpp: when another
# active publisher already supplied that class earlier in this same CfgVehicles
# section, DayZ reports "Member already defined".  Each adapter only knows its
# own dependencies, so deduplicate their top-level declarations when composing.
_TOP_FORWARD = re.compile(r"^    class ([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")
_TOP_DEFINITION = re.compile(r"^    class ([A-Za-z_][A-Za-z0-9_]*)(?:\s*:\s*[A-Za-z_][A-Za-z0-9_]*)?\s*$")


def _compose_vehicle_classes(*fragments: str) -> str:
    """Keep one forward declaration per class, and never emit one after a body.

    A forward before a full definition remains legal and may be needed by a
    preceding subclass.  Duplicate full definitions are rejected instead of
    silently deleting potentially different publisher configuration.
    """
    declared: set[str] = set()
    defined: set[str] = set()
    result: list[str] = []
    for line in "".join(fragments).splitlines(keepends=True):
        if forward := _TOP_FORWARD.fullmatch(line.rstrip("\r\n")):
            name = forward.group(1).casefold()
            if name in declared or name in defined:
                continue
            declared.add(name)
        elif definition := _TOP_DEFINITION.fullmatch(line.rstrip("\r\n")):
            name = definition.group(1).casefold()
            if name in defined:
                raise ValueError(f"Vehicle compatibility defines {name} more than once in CfgVehicles.")
            defined.add(name)
        result.append(line)
    return "".join(result)


def _render_parts(root: Path, active_workshop_ids: Iterable[str]) -> tuple[str, str]:
    from azimuth_app.transport import vehicle_base_compat, vehicle_asset_compat, vehicle_wrap_model_compat
    active = tuple(active_workshop_ids)
    slot_patch, slots = _render_slot_parts(root, active)
    base_patch, bases = vehicle_base_compat.config_parts(root, active)
    asset_patch, assets = vehicle_asset_compat.config_parts(active)
    model_patch, models = vehicle_wrap_model_compat.config_parts(root, active)
    patch = slot_patch + base_patch + asset_patch + model_patch
    vehicles = "class CfgVehicles\n{\n" + _compose_vehicle_classes(slots, bases, assets, models) + "};\n" if patch else ""
    return patch, vehicles


def config_fragment(root: Path, active_workshop_ids: Iterable[str]) -> str:
    """Standalone fragment; dependencies refer only to relevant active publishers."""
    patch, vehicles = _render_parts(root, active_workshop_ids)
    return "class CfgPatches\n{\n" + patch + "};\n" + vehicles if patch else ""


def apply_config(original: str, root: Path, active_workshop_ids: Iterable[str]) -> str:
    """Add one patch within the existing random-skin CfgPatches, then cargo edits."""
    if re.search(rf"\bclass\s+(?:{PATCH}|Azimuth_VehicleBaseIsolation|Azimuth_VehicleScalarIsolation|Azimuth_VehicleAssetCompatibility|Azimuth_VehicleWrapModelCompatibility)\b", original):
        raise ValueError("Vehicle slot compatibility is already present in the source.")
    patch, vehicles = _render_parts(root, active_workshop_ids)
    if not patch:
        return original
    # The owned random-skin source has one CfgPatches and no CfgVehicles section.
    matches = list(re.finditer(r"\bclass\s+CfgPatches\s*\{", original))
    if len(matches) != 1 or re.search(r"\bclass\s+CfgVehicles\b", original):
        raise ValueError("Vehicle slot compatibility requires one owned CfgPatches and no CfgVehicles section.")
    at = matches[0].end()
    return original[:at] + "\n" + patch + original[at:] + "\n" + vehicles
