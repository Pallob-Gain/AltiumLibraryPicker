"""Resolve a manually downloaded package into source SchLib / PcbLib / IntLib / STEP files.

Accepts: .SchLib, .PcbLib, .IntLib, .LibPkg, .zip, or a folder.
Zip files are extracted safely (no absolute paths, no '..', no symlinks, size limits).
"""

from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

MAX_MEMBERS = 2000
MAX_TOTAL_BYTES = 500 * 1024 * 1024
LIB_EXTS = {".schlib", ".pcblib", ".intlib"}
STEP_EXTS = {".step", ".stp"}


@dataclass
class SourcePackage:
    schlib: Path | None = None
    pcblib: Path | None = None
    intlib: Path | None = None
    step_files: list[Path] = field(default_factory=list)
    epw_files: list[Path] = field(default_factory=list)   # SamacSys part file (part ID)
    notes: list[str] = field(default_factory=list)


class UnsafeArchive(Exception):
    pass


def safe_extract(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=False)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_MEMBERS:
            raise UnsafeArchive(f"Archive has too many entries ({len(infos)}).")
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise UnsafeArchive("Archive expands to more than 500 MB.")
        for info in infos:
            name = info.filename.replace("\\", "/")
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or re.match(r"^[A-Za-z]:", name):
                raise UnsafeArchive(f"Unsafe path in archive: {info.filename}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise UnsafeArchive(f"Symbolic link in archive: {info.filename}")
            target = (root / pure).resolve()
            if root != target and root not in target.parents:
                raise UnsafeArchive(f"Unsafe path in archive: {info.filename}")
        zf.extractall(root)
    return root


def _scan(folder: Path, pkg: SourcePackage) -> None:
    files = [p for p in folder.rglob("*") if p.is_file()]
    found: dict[str, list[Path]] = {".schlib": [], ".pcblib": [], ".intlib": []}
    for p in files:
        ext = p.suffix.lower()
        if ext in found:
            found[ext].append(p)
        elif ext in STEP_EXTS:
            pkg.step_files.append(p)
        elif ext == ".epw" and p.stat().st_size < 1_000_000:
            pkg.epw_files.append(p)
    for ext, attr in ((".schlib", "schlib"), (".pcblib", "pcblib"), (".intlib", "intlib")):
        items = sorted(found[ext])
        if items and getattr(pkg, attr) is None:
            setattr(pkg, attr, items[0])
            if len(items) > 1:
                pkg.notes.append(f"{len(items)} {ext} files found; using {items[0].name}. "
                                 "Select another one manually if needed.")


def _libpkg_documents(libpkg: Path) -> list[Path]:
    docs = []
    for line in libpkg.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("DocumentPath="):
            rel = line.split("=", 1)[1].strip()
            if rel:
                p = (libpkg.parent / rel)
                if p.is_file():
                    docs.append(p)
    return docs


def resolve_source(path: Path, extract_root: Path) -> SourcePackage:
    path = Path(path)
    pkg = SourcePackage()
    ext = path.suffix.lower()
    if path.is_dir():
        _scan(path, pkg)
    elif ext == ".schlib":
        pkg.schlib = path
        _scan(path.parent, pkg)
    elif ext == ".pcblib":
        pkg.pcblib = path
        _scan(path.parent, pkg)
    elif ext == ".intlib":
        pkg.intlib = path
    elif ext == ".libpkg":
        docs = _libpkg_documents(path)
        for d in docs:
            if d.suffix.lower() == ".schlib" and pkg.schlib is None:
                pkg.schlib = d
            elif d.suffix.lower() == ".pcblib" and pkg.pcblib is None:
                pkg.pcblib = d
        if not docs:
            pkg.notes.append("The LibPkg lists no documents; using the libraries in its folder.")
        _scan(path.parent, pkg)
    elif ext == ".zip":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        out = safe_extract(path, extract_root / f"{path.stem}-{stamp}")
        pkg.notes.append(f"Extracted to {out}")
        _scan(out, pkg)
    else:
        raise ValueError(f"Unsupported file type: {path.name}")
    if pkg.intlib and (pkg.schlib or pkg.pcblib) and ext != ".intlib":
        pkg.intlib = None   # separate libraries take precedence over an IntLib in the same folder
    return pkg
