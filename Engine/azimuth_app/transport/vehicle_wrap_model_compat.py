"""Expose compiled exterior sections omitted from captured publisher configs.

Only append hidden-selection names. Existing indices, model defaults, damage
materials, transport classes and saved entities are retained. Model geometry
evidence for every appended name is shipped beside the exact class records.
"""
from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
import re
import stat

from azimuth_app.core.backups import source_type

DATA = Path("Custom/Transport/Workshop/wrap_model_compat.json")
PATCH = "Azimuth_VehicleWrapModelCompatibility"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
WORKSHOP_ID = re.compile(r"[0-9]{6,20}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
MAX_DATA_BYTES = 512 * 1024


def _names(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("Vehicle exterior compatibility has an invalid name list.")
    if any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v) for v in value):
        raise ValueError("Vehicle exterior compatibility has an invalid config name.")
    if len({v.lower() for v in value}) != len(value):
        raise ValueError("Vehicle exterior compatibility has duplicate config names.")
    return value


def load(root: Path) -> list[dict]:
    path = root / DATA
    if not stat.S_ISREG(source_type(path)):
        raise ValueError("Vehicle exterior compatibility must be an ordinary file.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_DATA_BYTES + 1)
    if len(raw) > MAX_DATA_BYTES:
        raise ValueError("Vehicle exterior compatibility exceeds its size limit.")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Vehicle exterior compatibility has an unsupported version.")
    rows = data.get("classes")
    models = data.get("models")
    if not isinstance(rows, list) or len(rows) > 256 or not isinstance(models, list):
        raise ValueError("Vehicle exterior compatibility has invalid captured evidence.")
    proofs = {}
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("model"), str):
            raise ValueError("Vehicle exterior compatibility has invalid model evidence.")
        if not HASH.fullmatch(str(model.get("sha256", ""))):
            raise ValueError("Vehicle exterior compatibility needs captured model hashes.")
        proofs[model["model"]] = model
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Vehicle exterior compatibility has an invalid class.")
        _names([row.get("name"), row.get("parent")])
        key = row["name"].lower()
        if key in seen:
            raise ValueError("Vehicle exterior compatibility repeats a class.")
        seen.add(key)
        if not WORKSHOP_ID.fullmatch(str(row.get("workshop_id", ""))):
            raise ValueError("Vehicle exterior compatibility needs exact Workshop IDs.")
        if not _names(row.get("required_addons")) or not _names(row.get("append")):
            raise ValueError("Vehicle exterior compatibility needs dependencies and exterior names.")
        if not HASH.fullmatch(str(row.get("source_config_sha256", ""))):
            raise ValueError("Vehicle exterior compatibility needs captured config hashes.")
        proof = proofs.get(row.get("model"))
        if not proof or proof["sha256"] != row.get("source_model_sha256"):
            raise ValueError("Vehicle exterior compatibility is missing matching model evidence.")
        original = row.get("original_selections")
        if not isinstance(original, list) or any(not isinstance(n, str) for n in original):
            raise ValueError("Vehicle exterior compatibility needs captured selection order.")
        if {n.lower() for n in original}.intersection(n.lower() for n in row["append"]):
            raise ValueError("Vehicle exterior compatibility may only append missing sections.")
        sections = {s["name"] for s in proof.get("selections", []) if isinstance(s, dict)}
        if any(n not in sections for n in row["append"]):
            raise ValueError("Vehicle exterior compatibility references an unproven model section.")
    return rows


def config_parts(root: Path, active_workshop_ids: Iterable[str]) -> tuple[str, str]:
    active = set(active_workshop_ids)
    if any(not isinstance(v, str) or not WORKSHOP_ID.fullmatch(v) for v in active):
        raise ValueError("Active vehicle Workshop IDs must be numeric strings.")
    if not active:
        return "", ""
    rows = [row for row in load(root) if row["workshop_id"] in active]
    if not rows:
        return "", ""
    deps = sorted({name for row in rows for name in row["required_addons"]}, key=str.lower)
    required = ", ".join(json.dumps(name) for name in deps)
    patch = f'''    class {PATCH}
    {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{{required}}};
    }};
'''
    lines = ["    // Model-proven exterior sections; preserve every native slot index."]
    lines += [f"    class {parent};" for parent in sorted({r["parent"] for r in rows})]
    for row in rows:
        values = ", ".join(json.dumps(name) for name in row["append"])
        lines += [f'    class {row["name"]}: {row["parent"]}', "    {",
                  f"        hiddenSelections[] += {{{values}}};", "    };"]
    return patch, "\n".join(lines) + "\n"
