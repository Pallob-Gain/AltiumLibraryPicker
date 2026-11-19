"""READ-ONLY lister for Altium .SchLib files: symbol names and their footprint links.

A .SchLib is an OLE compound file. Each symbol has a storage whose "Data" stream holds records
"|KEY=VALUE|..." prefixed by a 4-byte header (low 3 bytes = length, high byte = type, 0 = text).
RECORD=1 is the component (LibReference), RECORD=45 an implementation (ModelName/ModelType).

This module never writes Altium files - all modifications go through Altium's scripting API.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

_END = 0xFFFFFFFA   # sector ids >= this are special (end of chain, free, FAT, DIFAT)


class NotAnAltiumLibrary(Exception):
    pass


class _Cfb:
    """Minimal compound-file reader (enough to read named streams)."""

    def __init__(self, data: bytes):
        if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            raise NotAnAltiumLibrary("not an OLE compound file")
        self.d = data
        self.ss = 1 << struct.unpack_from("<H", data, 30)[0]
        self.mss = 1 << struct.unpack_from("<H", data, 32)[0]
        self.cutoff = struct.unpack_from("<I", data, 0x38)[0]
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        nxt = struct.unpack_from("<I", data, 0x44)[0]
        while nxt < _END:
            s = self._sec(nxt)
            difat += struct.unpack_from(f"<{self.ss // 4 - 1}I", s)
            nxt = struct.unpack_from("<I", s, self.ss - 4)[0]
        self.fat: list[int] = []
        for f in difat:
            if f < _END:
                self.fat += struct.unpack(f"<{self.ss // 4}I", self._sec(f))
        dirb = b"".join(self._sec(i) for i in self._chain(struct.unpack_from("<I", data, 0x30)[0],
                                                           self.fat))
        self.ents = []
        for i in range(len(dirb) // 128):
            e = dirb[i * 128:(i + 1) * 128]
            nl = struct.unpack_from("<H", e, 64)[0]
            left, right, child = struct.unpack_from("<III", e, 68)
            start, size = struct.unpack_from("<IQ", e, 116)
            self.ents.append({"name": e[:max(nl - 2, 0)].decode("utf-16-le", "replace"),
                              "type": e[66], "l": left, "r": right, "c": child,
                              "start": start, "size": size & 0xFFFFFFFF})
        root = self.ents[0]
        self.ministream = b"".join(self._sec(i) for i in self._chain(root["start"], self.fat))
        self.minifat: list[int] = []
        for i in self._chain(struct.unpack_from("<I", data, 0x3C)[0], self.fat):
            self.minifat += struct.unpack(f"<{self.ss // 4}I", self._sec(i))

    def _sec(self, i: int) -> bytes:
        return self.d[(i + 1) * self.ss:(i + 2) * self.ss]

    @staticmethod
    def _chain(start: int, table: list[int]) -> list[int]:
        out, seen = [], set()
        while start < _END and start < len(table) and start not in seen:
            seen.add(start)
            out.append(start)
            start = table[start]
        return out

    def children(self, idx: int) -> dict[str, int]:
        out: dict[str, int] = {}
        stack, seen = [self.ents[idx]["c"]], set()
        while stack:
            j = stack.pop()
            if j >= _END or j >= len(self.ents) or j in seen:
                continue
            seen.add(j)
            out[self.ents[j]["name"]] = j
            stack += [self.ents[j]["l"], self.ents[j]["r"]]
        return out

    def read(self, idx: int) -> bytes:
        e = self.ents[idx]
        if e["size"] < self.cutoff:
            return b"".join(self.ministream[i * self.mss:(i + 1) * self.mss]
                            for i in self._chain(e["start"], self.minifat))[:e["size"]]
        return b"".join(self._sec(i) for i in self._chain(e["start"], self.fat))[:e["size"]]


@dataclass
class SymbolInfo:
    name: str
    description: str = ""
    footprints: list[str] = field(default_factory=list)


def _records(stream: bytes):
    pos = 0
    while pos + 4 <= len(stream):
        header = struct.unpack_from("<I", stream, pos)[0]
        length, kind = header & 0xFFFFFF, header >> 24
        body = stream[pos + 4:pos + 4 + length]
        pos += 4 + length
        if kind != 0:
            continue
        rec = {}
        for part in body.rstrip(b"\0").decode("cp1252", "replace").split("|"):
            k, sep, v = part.partition("=")
            if sep and k not in rec:
                rec[k] = v
        yield rec


def list_symbols(path: str | Path) -> list[SymbolInfo]:
    """Symbols in a .SchLib, in file order of storage names, with their PCB footprint names."""
    cfb = _Cfb(Path(path).read_bytes())
    out = []
    for name, idx in sorted(cfb.children(0).items()):
        if cfb.ents[idx]["type"] != 1:      # storages only
            continue
        data = cfb.children(idx).get("Data")
        if data is None:
            continue
        info = None
        for rec in _records(cfb.read(data)):
            kind = rec.get("RECORD")
            if kind == "1" and info is None:
                info = SymbolInfo(rec.get("LibReference", name),
                                  rec.get("ComponentDescription", ""))
            elif kind == "45" and info is not None and rec.get("ModelType", "").upper() == "PCBLIB":
                fp = rec.get("ModelName", "")
                if fp and fp not in info.footprints:
                    info.footprints.append(fp)
        if info is not None:
            out.append(info)
    return out


def best_match(symbols: list[str], *candidates: str) -> str:
    """Symbol for the first candidate (MPN, LCSC number...) that identifies exactly one symbol.

    Exact name (case/whitespace-insensitive) wins; otherwise a symbol whose '_'-separated name
    parts include the candidate (EasyEDA names symbols '<MPN>_<LCSC>'). '' if none or ambiguous.
    """
    norm = lambda s: "".join(s.split()).upper()   # noqa: E731
    for c in candidates:
        if not c:
            continue
        exact = [s for s in symbols if norm(s) == norm(c)]
        if len(exact) == 1:
            return exact[0]
        parts = [s for s in symbols
                 if norm(c) in norm(s).split("_") or norm(s).startswith(norm(c) + "_")]
        if len(parts) == 1:
            return parts[0]
    return ""
