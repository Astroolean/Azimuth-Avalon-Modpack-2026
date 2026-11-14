"""Remember which DayZ profile and graphics preset Azimuth should use."""

from dataclasses import dataclass
import json
from pathlib import Path

from azimuth_app.core.worlds import atomic_text


VISUAL = "visual"
PERFORMANCE = "performance"
BALANCED = "balanced"
CUSTOM = "custom"
MODE_LABELS = {VISUAL: "Avalon Visuals", PERFORMANCE: "FPS preset", BALANCED: "Balanced graphics",
               CUSTOM: "Your DayZ settings"}
GRAPHICS_SETTINGS = "client-graphics.json"


@dataclass(frozen=True)
class GraphicsSettings:
    mode: str = VISUAL
    profile: Path | None = None


def read_settings(config: Path) -> GraphicsSettings:
    path = config / GRAPHICS_SETTINGS
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return GraphicsSettings()
    except (UnicodeError, ValueError) as error:
        raise ValueError(f"Invalid {GRAPHICS_SETTINGS}.") from error
    if (not isinstance(data, dict) or not isinstance(data.get("mode"), str) or data["mode"] not in MODE_LABELS
            or not isinstance(data.get("profile"), (str, type(None)))):
        raise ValueError(f"Invalid {GRAPHICS_SETTINGS}.")
    selected = data.get("profile")
    profile = Path(selected) if selected else None
    return GraphicsSettings(data["mode"], profile)


def write_settings(config: Path, mode: str, profile: Path | None) -> None:
    if mode not in MODE_LABELS:
        raise ValueError("Choose a supported DayZ graphics preset.")
    config.mkdir(parents=True, exist_ok=True)
    data = {"mode": mode, "profile": str(profile.resolve()) if profile is not None else None}
    path = config / GRAPHICS_SETTINGS
    text = json.dumps(data, indent=2) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        atomic_text(path, text)
