"""Narrow wrap cleanup adapters for the inspected LM aircraft script classes.

The publisher's plane base overrides EEDelete without delegating to CarScript.
Keep its own cleanup, but release Azimuth's two queued callbacks explicitly.
No publisher source, physics, persistence layout or constructor is replaced.
"""
from __future__ import annotations

from collections.abc import Iterable
import json
import re

WORKSHOP_ID = "3639695989"
PATCH = "Azimuth_LM_WrapCleanup"
SCRIPT = "scripts/4_world/azimuth_lm_wrap_cleanup.c"
PUBLIC_CLASSES = (
    "LM_Cessna180", "LM_Spitfire", "LM_Catalina", "LM_StuntPlane",
    "LM_Tigermoth", "LM_Tigermoth_MK2", "LM_Tigermoth_MK3",
    "LM_Patty_Wagon", "LM_Z37_Bumblebee", "LM_DC_3",
)
REQUIRED_ADDONS = (
    "LM_Planes", "LM_Cessna180", "LM_Spitfire", "LM_Catalina",
    "LM_StuntPlane", "LM_Tigermoth", "LM_Patty_Wagon",
    "LM_Z37_Bumblebee", "LM_DC_3",
)


def script_files(active_workshop_ids: Iterable[str]) -> dict[str, bytes]:
    if WORKSHOP_ID not in set(active_workshop_ids):
        return {}
    blocks = [
        "// LM's inspected plane base does not call super.EEDelete.\n"
        "// Remove only Azimuth callbacks, then preserve publisher cleanup.\n"
    ]
    for name in PUBLIC_CLASSES:
        blocks.append(f"""modded class {name}
{{
    override void EEDelete(EntityAI parent)
    {{
        AzimuthVehicleWrapCleanup();
        super.EEDelete(parent);
    }}
}}
""")
    return {SCRIPT: "\n".join(blocks).encode("utf-8")}


def apply_config(original: str, active_workshop_ids: Iterable[str]) -> str:
    if WORKSHOP_ID not in set(active_workshop_ids):
        return original
    if re.search(rf"\bclass\s+{PATCH}\b", original):
        raise ValueError("LM wrap cleanup is already present in the source configuration.")
    matches = list(re.finditer(r"\bclass\s+CfgPatches\s*\{", original))
    if len(matches) != 1:
        raise ValueError("LM wrap cleanup requires the owned CfgPatches section.")
    required = ", ".join(json.dumps(name) for name in REQUIRED_ADDONS)
    patch = f"""
    class {PATCH}
    {{
        units[] = {{}};
        weapons[] = {{}};
        requiredVersion = 0.1;
        requiredAddons[] = {{{required}}};
    }};
"""
    at = matches[0].end()
    return original[:at] + patch + original[at:]
