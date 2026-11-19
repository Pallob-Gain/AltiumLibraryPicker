"""Application paths and persistent user settings (JSON, written atomically)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import APP_NAME

log = logging.getLogger(__name__)


def app_dir() -> Path:
    """Per-user data folder. Override with the ALM_HOME environment variable (used by tests)."""
    base = os.environ.get("ALM_HOME") or os.path.join(
        os.environ.get("APPDATA") or str(Path.home()), APP_NAME
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: Path, text: str) -> None:
    """Write to a temp file then replace, so a crash never leaves a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class Settings:
    DEFAULTS = {"schlib": "", "pcblib": "", "last_mpn": "", "last_download_dir": "",
                "on_conflict": "skip", "link_mode": "name", "x2_path": ""}

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_dir() / "settings.json"
        self.data: dict = dict(self.DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("Settings file unreadable, using defaults: %s", self.path)

    def save(self) -> None:
        atomic_write_text(self.path, json.dumps(self.data, indent=2))

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()
