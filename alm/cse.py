"""Component Search Engine (SamacSys) - browser part of the workflow.

The app opens the CSE search for the MPN in the user's browser and watches the Downloads
folder. What arrives is either
  * a full library download (zip with SchLib/PcbLib or IntLib) -> imported directly, or
  * a small ".epw" part file (what CSE's download button gives Library Loader users) -> the app
    downloads the model itself with the user's saved CSE login (alm/cse_client.py), then imports.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CSE_HOME = "https://componentsearchengine.com/"
ACCEPTED = (".zip", ".intlib", ".schlib", ".pcblib", ".libpkg", ".epw")
PARTIAL = (".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp")


def default_downloads_dir() -> Path:
    """The user's real Downloads folder (it can be moved), falling back to ~/Downloads."""
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                            ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
            # FOLDERID_Downloads {374DE290-123F-4565-9164-39C4925E467B}
            guid = GUID(0x374DE290, 0x123F, 0x4565,
                        (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None,
                                                          ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path:
                    return Path(path)
        except (OSError, AttributeError):
            pass
    return Path.home() / "Downloads"


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def name_matches(file_name: str, mpn: str) -> bool:
    """True if the file name contains the MPN (letters/digits only, case-insensitive)."""
    m = _norm(mpn)
    return bool(m) and m in _norm(Path(file_name).stem)


@dataclass
class WatchStatus:
    state: str                 # waiting | downloading | ready | timeout
    path: Path | None = None
    size: int = 0
    matches_mpn: bool = False
    message: str = ""


class DownloadWatcher:
    """Poll a folder for a library file that appears AFTER the watch started.

    A file is 'ready' when it has an accepted extension, no browser partial-download file is
    pending for it, and its size has not changed for `stable_secs`.
    """

    def __init__(self, folder: Path, mpn: str, timeout: float = 900, stable_secs: float = 2.0,
                 clock=time.time):
        self.folder, self.mpn, self.timeout, self.stable_secs = Path(folder), mpn, timeout, stable_secs
        self.clock = clock
        self.started = clock()
        self._known = self._listing()          # files present before the watch: ignored
        self._seen: dict[str, tuple[int, float]] = {}   # name -> (size, time size last changed)

    def _listing(self) -> dict[str, tuple[int, float]]:
        out = {}
        try:
            with os.scandir(self.folder) as it:
                for e in it:
                    if e.is_file():
                        st = e.stat()
                        out[e.name] = (st.st_size, st.st_mtime)
        except OSError:
            pass
        return out

    def poll(self) -> WatchStatus:
        now = self.clock()
        files = self._listing()
        new = {n: v for n, v in files.items() if n not in self._known or v != self._known[n]}
        partial = [n for n in new if n.lower().endswith(PARTIAL)]
        done = [n for n in new if n.lower().endswith(ACCEPTED)]
        for n in done:
            size = new[n][0]
            prev = self._seen.get(n)
            if prev is None or prev[0] != size:
                self._seen[n] = (size, now)
        # prefer files named after the MPN, then the newest
        done.sort(key=lambda n: (name_matches(n, self.mpn), new[n][1]), reverse=True)
        for n in done:
            size, since = self._seen[n]
            stem = n.lower()
            still_partial = any(p.lower().startswith(stem) for p in partial)
            if size > 0 and not still_partial and now - since >= self.stable_secs \
                    and _readable(self.folder / n):
                return WatchStatus("ready", self.folder / n, size, name_matches(n, self.mpn))
        if partial or done:
            name = (partial or done)[0]
            return WatchStatus("downloading", self.folder / name, new[name][0],
                               message=f"Downloading {_display(name)}")
        if now - self.started > self.timeout:
            return WatchStatus("timeout", message="No download arrived in "
                               f"{int(self.timeout // 60)} minutes.")
        return WatchStatus("waiting", message=f"Waiting for the download in {self.folder}")


def _display(name: str) -> str:
    low = name.lower()
    for suf in PARTIAL:
        if low.endswith(suf):
            return name[: -len(suf)]
    return name


def _readable(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            fh.read(1)
        return True
    except OSError:          # still locked by the browser / antivirus
        return False


def claim(path: Path, work_dir: Path) -> Path:
    """Copy the finished download into the app's own folder right away (the official Library
    Loader may move files out of Downloads), and return the copy."""
    dest = work_dir / "downloads" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / path.name
    shutil.copy2(path, target)
    return target


def human_size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} kB"
