"""Import orchestration: validate -> back up destination -> run Altium job -> record in DB."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .altium import bridge
from .validation import check_library

STANDARD_PARAMS = ("Manufacturer", "Manufacturer Part Number", "Datasheet", "LCSC Part #")


@dataclass
class ImportRequest:
    src_schlib: str
    src_pcblib: str
    dest_schlib: str
    dest_pcblib: str
    params: dict[str, str] = field(default_factory=dict)   # standard name -> value ('' allowed)
    description: str = ""
    on_conflict: str = "skip"      # skip | rename
    link_mode: str = "name"        # name | path | any
    source_label: str = ""
    component: str = ""            # '' = every symbol in the source SchLib
    step_file: str = ""            # STEP attached to new footprints that have no 3D body


def validate(req: ImportRequest) -> list[str]:
    errors = []
    for label, path, ext in (("Source SchLib", req.src_schlib, ".SchLib"),
                             ("Source PcbLib", req.src_pcblib, ".PcbLib"),
                             ("Destination SchLib", req.dest_schlib, ".SchLib"),
                             ("Destination PcbLib", req.dest_pcblib, ".PcbLib")):
        err = check_library(path, ext)
        if err:
            errors.append(f"{label}: {err}")
    if errors:
        return errors
    for a, b in ((req.src_schlib, req.dest_schlib), (req.src_pcblib, req.dest_pcblib)):
        if os.path.samefile(a, b):
            errors.append(f"Source and destination are the same file: {a}")
    if req.on_conflict not in ("skip", "rename"):
        errors.append("Invalid conflict policy.")
    if req.link_mode not in ("name", "path", "any"):
        errors.append("Invalid footprint link mode.")
    return errors


def backup(paths: list[str], backup_dir: Path) -> list[Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = backup_dir / stamp
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for p in paths:
        target = folder / Path(p).name
        shutil.copy2(p, target)
        out.append(target)
    return out


def job_values(req: ImportRequest) -> dict[str, str]:
    values = {"Action": "import", "SrcSchLib": req.src_schlib, "SrcPcbLib": req.src_pcblib,
              "DestSchLib": req.dest_schlib, "DestPcbLib": req.dest_pcblib,
              "OnConflict": req.on_conflict, "LinkMode": req.link_mode,
              "Description": req.description, "Component": req.component,
              "StepFile": req.step_file,
              "KeepOpen": "1"}   # leave saved libs loaded; 'finish' closes them (see run_import)
    names = [n for n in STANDARD_PARAMS] + [n for n in req.params if n not in STANDARD_PARAMS]
    values["ParamCount"] = str(len(names))
    for i, name in enumerate(names, 1):
        values[f"Param{i}Name"] = name
        values[f"Param{i}Value"] = req.params.get(name, "")
    return values


def run_import(req: ImportRequest, x2: Path, work_dir: Path,
               timeout: float = 240) -> bridge.ImportReport:
    """Safe to call from a worker thread (no database access)."""
    errors = validate(req)
    if errors:
        return bridge.ImportReport(errors=errors)
    # Separate Altium runs, because of how AD26 refreshes its library search (verified 2026-11-19):
    # the refresh happens only after the saving script run has ENDED, it reads the saved document,
    # so that document must still be loaded then, and it must not be open in an editor while saved.
    # Otherwise the new footprint stays "not found" until Altium is restarted.
    # 1) prepare: close destinations open in an editor (refused if they have unsaved changes)
    dest = {"DestSchLib": req.dest_schlib, "DestPcbLib": req.dest_pcblib}
    prep = bridge.run_job(x2, work_dir, {"Action": "prepare", **dest}, timeout)
    if not prep.ok:
        return prep
    if prep.flags:
        # Altium finishes closing documents only after the script run ends; confirm they are closed
        for _ in range(5):
            time.sleep(1.5)
            again = bridge.run_job(x2, work_dir, {"Action": "prepare", **dest}, timeout)
            if not again.ok:
                return again
            if not again.flags:
                break
    # 2) import (saved libraries stay loaded, hidden)
    report = bridge.ImportReport(errors=["Import did not run."])
    try:
        backups = backup([req.dest_schlib, req.dest_pcblib], work_dir / "backups")
        report = bridge.run_job(x2, work_dir, job_values(req), timeout)
        report.backups = backups
    finally:
        # 3) finish (always): check Altium's library search, close the hidden libraries,
        #    reopen what the user had open
        time.sleep(2)
        _finish(req, prep, report, x2, work_dir, timeout)
    return report


def finish_values(req: ImportRequest, prep: bridge.ImportReport,
                  report: bridge.ImportReport) -> dict[str, str]:
    footprints = sorted({f for _, f, _ in report.links if f}) if report.ok else []
    values = {"Action": "finish", "DestSchLib": req.dest_schlib, "DestPcbLib": req.dest_pcblib,
              "FootprintCount": str(len(footprints)), "CloseDocs": "1",
              "ReopenSch": "1" if prep.flags.get("SCH_WAS_OPEN") == "1" else "0",
              "ReopenPcb": "1" if prep.flags.get("PCB_WAS_OPEN") == "1" else "0"}
    for i, fp in enumerate(footprints, 1):
        values[f"Footprint{i}"] = fp
    return values


def _finish(req, prep, report, x2, work_dir, timeout) -> None:
    try:
        fin = bridge.run_job(x2, work_dir, finish_values(req, prep, report), timeout)
        report.cache = fin.cache
        report.warnings += fin.errors + fin.warnings
    except bridge.AltiumError as e:
        report.warnings.append(f"Could not check Altium's library search / reopen libraries: {e}")


def record_import(db, req: ImportRequest, report: bridge.ImportReport) -> None:
    """Store a successful import in the local component database (call from the DB's thread)."""
    if report.ok and report.added_symbols:
        footprints = sorted({f for _, f, _ in report.links})
        db.add_component(
            req.params.get("Manufacturer Part Number") or report.added_symbols[0],
            files=[req.src_schlib, req.src_pcblib],
            manufacturer=req.params.get("Manufacturer", ""),
            lcsc_part=req.params.get("LCSC Part #", ""),
            source=req.source_label,
            dest_schlib=req.dest_schlib, dest_pcblib=req.dest_pcblib,
            symbol_name=", ".join(report.added_symbols),
            footprint_name=", ".join(footprints),
            verification=("pins/pads match (auto); function unverified" if report.pins_match
                          else "NEEDS MANUAL REVIEW"),
        )
