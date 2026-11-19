"""LCSC number -> EasyEDA data -> part file -> staging SchLib/PcbLib built inside Altium.

The staging libraries are then imported into the user's destination with the normal, verified
import (backup, skip/rename, copy verification, pin check, library-search check).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..altium import bridge
from . import convert

UNOFFICIAL_NOTE = ("EasyEDA data comes from an UNDOCUMENTED EasyEDA API (community library). "
                   "Verify footprint and pinout against the datasheet.")


def clean_lcsc(text: str) -> str:
    """'c2040', ' C2040 ', '2040' -> 'C2040'; '' if it is not an LCSC part number."""
    t = text.strip().upper()
    if t.isdigit():
        t = "C" + t
    return t if re.fullmatch(r"C\d{1,12}", t) else ""


@dataclass
class PartFile:
    path: Path
    meta: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pins: int = 0
    pads: int = 0


def read_part_file(path: Path) -> PartFile:
    pf = PartFile(path)
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        f = line.split("\t")
        if f[0] == "META" and len(f) >= 3:
            pf.meta[f[1]] = f[2]
        elif f[0] == "PARAM" and len(f) >= 3:
            pf.params.setdefault(f[1], f[2])
        elif f[0] == "WARN" and len(f) >= 2:
            pf.warnings.append(f[1])
        elif f[0] == "SPIN":
            pf.pins += 1
        elif f[0] == "FPAD":
            pf.pads += 1
    return pf


def fetch(lcsc: str, base_dir: Path) -> PartFile:
    """Download + convert (network). Raises RuntimeError with EasyEDA's message on failure."""
    convert.fetch_and_convert(lcsc, str(base_dir))
    status = (base_dir / "cache" / f"{lcsc}.status.txt").read_text(encoding="utf-8-sig")
    state, _, rest = status.strip().partition("\t")
    if state != "OK":
        raise RuntimeError(rest or "EasyEDA download failed")
    return read_part_file(Path(rest))


@dataclass
class StagedPart:
    lcsc: str
    part: PartFile
    build: bridge.BuildResult
    schlib: Path
    pcblib: Path

    @property
    def problems(self) -> list[str]:
        out = list(self.build.errors)
        if self.build.ok and self.build.pads != self.part.pads:
            out.append(f"Footprint has {self.build.pads} pads but the EasyEDA data has "
                       f"{self.part.pads} - not used.")
        return out


def fetch_and_build(lcsc: str, base_dir: Path, x2: Path, work_dir: Path,
                    timeout: float = 240) -> StagedPart:
    part = fetch(lcsc, base_dir)
    stage = base_dir / "stage" / f"{lcsc}-{datetime.now():%Y%m%d-%H%M%S-%f}"
    stage.mkdir(parents=True)
    sch, pcb = stage / f"{lcsc}.SchLib", stage / f"{lcsc}.PcbLib"
    build = bridge.build_easyeda(x2, part.path, base_dir, sch, pcb, work_dir, timeout)
    return StagedPart(lcsc, part, build, sch, pcb)
