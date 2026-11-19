"""Runs alm_import.pas inside Altium Designer.

Mechanism (verified on AD26 26.10.1.5):
    X2.EXE -RScriptingSystem:RunScriptFile(FileName=<script.pas>|ProcName=Run)
If Altium is already running, the command is forwarded to that instance; otherwise
Altium starts first. The script reads job.txt and writes result.txt (last line END=1).
"""

from __future__ import annotations

import ctypes
import glob
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TEMPLATE = Path(__file__).with_name("alm_import.pas")


class AltiumError(Exception):
    pass


def find_x2(override: str = "") -> Path | None:
    """Locate X2.EXE: explicit setting, then the registry, then the default install folders."""
    if override and Path(override).is_file():
        return Path(override)
    candidates: list[Path] = []
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Altium\Builds") as builds:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(builds, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(builds, name) as k:
                            folder = winreg.QueryValueEx(k, "ProgramsInstallPath")[0]
                            candidates.append(Path(folder) / "X2.EXE")
                    except OSError:
                        pass
        except OSError:
            pass
    candidates += [Path(p) for p in sorted(glob.glob(r"C:\Program Files\Altium\AD*\X2.EXE"),
                                           reverse=True)]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _short_path(path: Path) -> str:
    """8.3 short path (no spaces) so the -R argument parses safely; falls back to the long path."""
    p = str(path)
    if " " not in p or sys.platform != "win32":
        return p
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024):
        return buf.value
    return p


def _pas_str(s: str) -> str:
    return s.replace("'", "''")


def _clean(value: str) -> str:
    return " ".join(str(value).split())   # no newlines inside key=value lines


def write_job(job_dir: Path, values: dict[str, str],
              template: Path = TEMPLATE) -> tuple[Path, Path, Path]:
    job_dir.mkdir(parents=True, exist_ok=True)
    job = job_dir / "job.txt"
    result = job_dir / "result.txt"
    script = job_dir / template.name
    job.write_text("".join(f"{k}={_clean(v)}\n" for k, v in values.items()), encoding="utf-8-sig")
    text = template.read_text(encoding="utf-8")
    text = (text.replace("{{JOB_FILE}}", _pas_str(_short_path(job)))
                .replace("{{RESULT_FILE}}", _pas_str(_short_path(job_dir) + "\\result.txt")))
    script.write_text(text, encoding="utf-8-sig")
    return job, script, result


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors="replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def run_script(x2: Path, script: Path, result: Path, timeout: float) -> str:
    if result.exists():
        result.unlink()
    arg = f"-RScriptingSystem:RunScriptFile(FileName={_short_path(script)}|ProcName=Run)"
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED, NEW_GROUP
    subprocess.Popen([str(x2), arg], creationflags=flags, close_fds=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result.exists():
            try:
                text = _decode(result.read_bytes())
            except PermissionError:      # Altium is still writing the file
                text = ""
            if "END=1" in text:
                return text
        time.sleep(0.5)
    raise AltiumError(f"No answer from Altium within {int(timeout)} s. Make sure Altium Designer is "
                      "running and no dialog is waiting in Altium, then retry.")


@dataclass
class ImportReport:
    status: str = "ERROR"
    added_symbols: list[str] = field(default_factory=list)
    added_footprints: list[str] = field(default_factory=list)
    added_3d: list[tuple[str, str]] = field(default_factory=list)   # (footprint, step file)
    existing_footprints: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    links: list[tuple[str, str, str]] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pinchecks: list[dict] = field(default_factory=list)
    cache: list[tuple[str, bool]] = field(default_factory=list)   # (footprint, found by Altium)
    flags: dict[str, str] = field(default_factory=dict)           # e.g. SCH_WAS_OPEN=1
    backups: list[Path] = field(default_factory=list)

    @property
    def cache_ok(self) -> bool | None:
        """True/False = Altium's library search does/doesn't find every footprint; None = not checked."""
        if not self.cache:
            return None
        return all(found for _, found in self.cache)

    @property
    def ok(self) -> bool:
        return self.status == "OK" and not self.errors

    @property
    def pins_match(self) -> bool:
        return bool(self.pinchecks) and all(
            p["pins"] not in ("", "0") and not p["pins_without_pad"] and not p["pads_without_pin"]
            for p in self.pinchecks)

    def summary(self) -> str:
        lines = [f"Result: {'SUCCESS' if self.ok else 'FAILED'}"]
        lines += [f"ERROR: {e}" for e in self.errors]
        lines += [f"Added symbol: {s}" for s in self.added_symbols]
        lines += [f"Added footprint: {f}" for f in self.added_footprints]
        lines += [f"Added 3D model: {s} -> {f} (placed at the footprint origin - check the 3D "
                  "view)" for f, s in self.added_3d]
        lines += [f"Skipped: {s}" for s in self.skipped]
        lines += [f"Linked: {s} -> {f} (library: {loc or 'any'})" for s, f, loc in self.links]
        lines += [f"Parameter: {p}" for p in self.params]
        lines += [f"Copy verified: {v}" for v in self.verified]
        for p in self.pinchecks:
            if p["pins"] in ("", "0"):
                lines.append(f"PIN CHECK NOT POSSIBLE ({p['symbol']}): no symbol pins found - "
                             "needs manual review.")
            elif p["pins_without_pad"] or p["pads_without_pin"]:
                lines.append(f"PIN/PAD MISMATCH ({p['symbol']} / {p['footprint']}) - needs manual "
                             f"review: pins without pad [{p['pins_without_pad']}], pads without pin "
                             f"[{p['pads_without_pin']}]")
            else:
                lines.append(f"Pin check ({p['symbol']} / {p['footprint']}): {p['pins']} pin "
                             f"designators all have pads ({p['pads']} pads). Automatic check only - "
                             "pin functions still need verification against the datasheet.")
        for fp, found in self.cache:
            if found:
                lines.append(f"Altium library search finds footprint {fp} (ready to place).")
            else:
                lines.append(f"NOTE: Altium's library search does not find footprint {fp}. This is "
                             "expected if the destination PcbLib is neither in an open project nor "
                             "installed (link mode 'Full path' still works). Otherwise restart Altium.")
        lines += [f"Warning: {w}" for w in self.warnings]
        lines += [f"Backup: {b}" for b in self.backups]
        return "\n".join(lines)


def parse_result(text: str) -> ImportReport:
    r = ImportReport()
    for line in text.splitlines():
        key, sep, val = line.partition("=")
        if not sep:
            continue
        key, val = key.strip(), val.strip()
        if key == "STATUS":
            r.status = val
        elif key == "ADDED_SYMBOL":
            r.added_symbols.append(val)
        elif key == "ADDED_FOOTPRINT":
            r.added_footprints.append(val)
        elif key == "ADDED_3D":
            fp, _, step = val.partition("|")
            r.added_3d.append((fp, step))
        elif key == "EXISTING_FOOTPRINT":
            r.existing_footprints.append(val)
        elif key == "SKIPPED_SYMBOL":
            r.skipped.append(val)
        elif key == "LINK":
            parts = (val.split("|") + ["", "", ""])[:3]
            r.links.append((parts[0], parts[1], parts[2]))
        elif key == "PARAM":
            r.params.append(val)
        elif key in ("VERIFY_SYMBOL", "VERIFY_FOOTPRINT"):
            r.verified.append(("Symbol " if key == "VERIFY_SYMBOL" else "Footprint ")
                              + val.replace("|", ", "))
        elif key == "WARNING":
            r.warnings.append(val)
        elif key == "ERROR":
            r.errors.append(val)
        elif key in ("SCH_WAS_OPEN", "PCB_WAS_OPEN"):
            r.flags[key] = val
        elif key == "VERIFY_CACHE":
            fp, _, state = val.rpartition("|")
            r.cache.append((fp, state == "ok"))
        elif key == "PINCHECK":
            p = (val.split("|") + [""] * 6)[:6]
            r.pinchecks.append({"symbol": p[0], "footprint": p[1], "pins": p[2], "pads": p[3],
                                "pins_without_pad": p[4], "pads_without_pin": p[5]})
    return r


def new_job_dir(work_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return work_dir / "jobs" / stamp


def run_job(x2: Path, work_dir: Path, values: dict[str, str], timeout: float = 240,
            template: Path = TEMPLATE) -> ImportReport:
    _, script, result = write_job(new_job_dir(work_dir), values, template)
    return parse_result(run_script(x2, script, result, timeout))


@dataclass
class BuildResult:
    ok: bool
    symbol: str = ""
    footprint: str = ""
    pads: int = 0
    primitives: int = 0
    info: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_build(text: str) -> BuildResult:
    vals: dict[str, list[str]] = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep:
            vals.setdefault(k.strip(), []).append(v.strip())
    one = lambda k: (vals.get(k) or [""])[0]   # noqa: E731
    num = lambda k: int(one(k)) if one(k).isdigit() else 0   # noqa: E731
    return BuildResult(ok=one("STATUS") == "OK" and not vals.get("ERROR"),
                       symbol=one("BUILT_SYMBOL"), footprint=one("BUILT_FOOTPRINT"),
                       pads=num("BUILT_PADS"), primitives=num("BUILT_PRIMITIVES"),
                       info=vals.get("INFO", []), warnings=vals.get("WARNING", []),
                       errors=vals.get("ERROR", []))


EE_TEMPLATE = Path(__file__).with_name("ee_build.pas")


def build_easyeda(x2: Path, part_file: Path, lib_dir: Path, stage_sch: Path, stage_pcb: Path,
                  work_dir: Path, timeout: float = 240) -> BuildResult:
    """Build one EasyEDA part file into NEW staging SchLib/PcbLib (EasyEDA-Importer's builder)."""
    values = {"PartFile": str(part_file), "LibDir": str(lib_dir),
              "StageSchLib": str(stage_sch), "StagePcbLib": str(stage_pcb)}
    _, script, result = write_job(new_job_dir(work_dir), values, EE_TEMPLATE)
    return parse_build(run_script(x2, script, result, timeout))


def extract_intlib(x2: Path, intlib: Path, out_dir: Path, work_dir: Path,
                   timeout: float = 240) -> ImportReport:
    out_dir.mkdir(parents=True, exist_ok=True)
    return run_job(x2, work_dir, {"Action": "extract", "IntLib": str(intlib),
                                  "OutDir": str(out_dir)}, timeout)


def is_altium_running() -> bool:
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/NH"], capture_output=True,
                         text=True, creationflags=0x08000000).stdout   # CREATE_NO_WINDOW
    return "X2.EXE" in out.upper()
