"""Stable installation paths shared by the application packages."""
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = APP_ROOT / "Engine"
ASSETS_ROOT = ENGINE_ROOT / "Assets"
