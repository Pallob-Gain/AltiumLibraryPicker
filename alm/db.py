"""Local SQLite database: imported components (F11) and a search log."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .ranking import normalize_mpn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY,
    mpn TEXT NOT NULL,
    mpn_norm TEXT NOT NULL,
    manufacturer TEXT NOT NULL DEFAULT '',
    lcsc_part TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    files_json TEXT NOT NULL DEFAULT '[]',
    dest_schlib TEXT NOT NULL DEFAULT '',
    dest_pcblib TEXT NOT NULL DEFAULT '',
    symbol_name TEXT NOT NULL DEFAULT '',
    footprint_name TEXT NOT NULL DEFAULT '',
    import_date TEXT NOT NULL,
    verification TEXT NOT NULL DEFAULT 'unverified'
);
CREATE INDEX IF NOT EXISTS ix_components_mpn ON components(mpn_norm);
CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    query TEXT NOT NULL,
    providers_json TEXT NOT NULL,
    exact_count INTEGER NOT NULL
);
"""

_COMPONENT_FIELDS = ("manufacturer", "lcsc_part", "source", "dest_schlib", "dest_pcblib",
                     "symbol_name", "footprint_name", "verification")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        with self.conn:
            self.conn.executescript(_SCHEMA)
            self.conn.execute("PRAGMA user_version = 1")

    def close(self) -> None:
        self.conn.close()

    def add_component(self, mpn: str, files: list[str] | None = None, **fields) -> int:
        unknown = set(fields) - set(_COMPONENT_FIELDS)
        if unknown:
            raise ValueError(f"Unknown component fields: {sorted(unknown)}")
        cols = ["mpn", "mpn_norm", "files_json", "import_date", *fields]
        vals = [mpn, normalize_mpn(mpn), json.dumps(files or []), _now(), *fields.values()]
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO components ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                vals)
        return int(cur.lastrowid)

    def find_component(self, mpn: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM components WHERE mpn_norm = ? ORDER BY import_date DESC",
            (normalize_mpn(mpn),)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["files"] = json.loads(d.pop("files_json") or "[]")
            out.append(d)
        return out

    def log_search(self, query: str, provider_states: dict[str, str], exact_count: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO search_log (ts, query, providers_json, exact_count) VALUES (?,?,?,?)",
                (_now(), query, json.dumps(provider_states), exact_count))
