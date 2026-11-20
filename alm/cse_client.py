"""Component Search Engine (SamacSys) model download - original implementation.

Protocol (verified live 2026-11-19: 401 without login, login accepted with a real account,
"Error: no part data available for partID N" for unknown IDs; same flow as Library Loader):
  1. On componentsearchengine.com the user clicks the download button for a part; the browser
     saves a small ".epw" file (or a zip holding one). Its first line is the numeric part ID,
     the other lines are key=value pairs (mpn=, mna=, ...).
  2. GET https://componentsearchengine.com/ga/model.php?partID=<id> with HTTP Basic
     authentication (the user's CSE email and password). Without valid credentials the server
     answers 401 with WWW-Authenticate: Basic realm="Samacsys Parts Database" (observed).
     With them it returns the part's model zip (one folder per ECAD format + 3D).
  3. The 3D placement (rotation/offset) is not in the zip - see placement() below.
The user stated (2026-11-19) that they have the owner's permission to use this access.

Security: credentials go only to https://componentsearchengine.com - redirects are refused, so
the Authorization header can never be forwarded elsewhere. Passwords are never logged and
never appear in error messages. Downloads are size-limited and must be real zip files.
"""

from __future__ import annotations

import base64
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

HOST = "componentsearchengine.com"
MODEL_URL = f"https://{HOST}/ga/model.php?partID="
# MPN search used by SamacSys' Altium integration (verified live 2026-11-20): JSON
# {"PartCount": n, "Parts": {"<id>": {"PartNumber", "Manufacturer", "ModelAvailable", ...}}}
SEARCH_URL = f"https://ad.{HOST}/ga/search.php"
USER_AGENT = f"AltiumLibraryManager/{__version__}"
MAX_DOWNLOAD = 150 * 1024 * 1024
CRED_EMAIL, CRED_PASSWORD = "cse_email", "cse_password"


class CseError(Exception):
    """Shown to the user; never contains the password."""


class CseAuthError(CseError):
    pass


# ----------------------------------------------------------------------------- .epw files
@dataclass
class Epw:
    part_id: int
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def mpn(self) -> str:
        return self.fields.get("mpn", "")

    @property
    def manufacturer(self) -> str:
        return self.fields.get("mna", "")


def parse_epw(text: str) -> Epw:
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].isdigit():
        raise CseError("Not a Component Search Engine .epw file (no part ID on the first line).")
    fields = {}
    for ln in lines[1:]:
        if ln.count("=") == 1:
            k, v = ln.split("=")
            fields[k.strip()] = v.strip()
    return Epw(int(lines[0]), fields)


def _open_zip(data: bytes) -> zipfile.ZipFile:
    # some downloads end with an extra newline after the zip data
    for candidate in (data, data.rstrip(b"\r\n")):
        try:
            return zipfile.ZipFile(io.BytesIO(candidate))
        except zipfile.BadZipFile:
            continue
    raise CseError("The downloaded zip file is damaged.")


def _zip_names(data: bytes) -> list[str]:
    with _open_zip(data) as z:
        return z.namelist()


def read_epw(path: Path) -> Epw | None:
    """Epw from an .epw file or a zip that holds one AND no library files; else None."""
    data = path.read_bytes()
    if path.suffix.lower() == ".epw":
        return parse_epw(data.decode("utf-8", "replace"))
    if path.suffix.lower() != ".zip":
        return None
    names = _zip_names(data)
    if any(n.lower().endswith((".schlib", ".pcblib", ".intlib", ".libpkg")) for n in names):
        return None                       # already a full library download
    epws = [n for n in names if n.lower().endswith(".epw")]
    if not epws:
        return None
    with _open_zip(data) as z:
        info = z.getinfo(epws[0])
        if info.file_size > 1_000_000:
            raise CseError("The .epw file in the zip is unexpectedly large.")
        return parse_epw(z.read(info).decode("utf-8", "replace"))


# ----------------------------------------------------------------------------- search
@dataclass
class CsePart:
    part_id: int
    mpn: str
    manufacturer: str = ""
    description: str = ""
    datasheet: str = ""
    model_available: bool = False
    has_3d: bool = False
    confidence: str = ""
    package: str = ""
    pins: str = ""


def _s(v) -> str:
    return "" if v in (None, "null") else str(v).strip()


def parse_search(data) -> list[CsePart]:
    """Parse search.php JSON. Raises CseError on an unexpected shape."""
    if not isinstance(data, dict) or "PartCount" not in data:
        raise CseError("Component Search Engine returned an unexpected search response.")
    parts = data.get("Parts") or {}
    if not isinstance(parts, dict):
        return []
    out = []
    for key, p in parts.items():
        if not isinstance(p, dict):
            continue
        pid = _s(p.get("PartID")) or str(key).removeprefix("pid")
        if not pid.isdecimal():
            continue
        out.append(CsePart(
            part_id=int(pid), mpn=_s(p.get("PartNumber")), manufacturer=_s(p.get("Manufacturer")),
            description=_s(p.get("Description")), datasheet=_s(p.get("DatasheetURL")),
            model_available=_s(p.get("ModelAvailable")) == "1" and bool(_s(p.get("Symbol")))
            and bool(_s(p.get("Footprint"))),
            has_3d=_s(p.get("Have3D")) == "1", confidence=_s(p.get("Confidence")),
            package=_s(p.get("PackageCategory")), pins=_s(p.get("PinCount"))))
    return out


def exact_matches(parts: list[CsePart], mpn: str) -> list[CsePart]:
    """Same MPN (case/whitespace-insensitive only - suffixes matter) with a downloadable model."""
    from .ranking import normalize_mpn
    want = normalize_mpn(mpn)
    return [p for p in parts if normalize_mpn(p.mpn) == want and p.model_available]


# ----------------------------------------------------------------------------- HTTP
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CseError(f"Component Search Engine redirected the request (HTTP {code}); "
                       "not followed so your login is never sent elsewhere. Sign in on the "
                       "website once and accept its terms, then retry.")


_opener = urllib.request.build_opener(_NoRedirect)


def _auth_header(email: str, password: str) -> str:
    token = base64.b64encode(f"{email}:{password}".encode("utf-8")).decode("ascii")
    return "Basic " + token


def safe_filename(content_disposition: str, part_id: int) -> str:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition or "", re.I)
    name = Path(m.group(1)).name if m else ""
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name.lower().endswith(".zip") or len(name) > 120:
        name = f"LIB_{part_id}.zip"
    return name


class CseClient:
    def __init__(self, email: str, password: str, timeout: float = 60):
        if not email or not password:
            raise CseAuthError("Not signed in: enter your Component Search Engine email and "
                               "password under Settings > Component Search Engine.")
        self._auth = _auth_header(email, password)
        self.timeout = timeout

    def search(self, mpn: str) -> list[CsePart]:
        """All CSE parts for an MPN keyword (use exact_matches() to filter)."""
        q = urllib.parse.urlencode({"kws": mpn.strip(), "v": 1})
        with self._open(url=f"{SEARCH_URL}?{q}") as resp:
            body = resp.read(5_000_000)
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            raise CseError("Component Search Engine search did not return data. Sign in on "
                           "componentsearchengine.com once in your browser, then retry.") from None
        return parse_search(data)

    def _open(self, part_id: int = 0, url: str = ""):
        req = urllib.request.Request(url or MODEL_URL + str(int(part_id)),
                                     headers={"Authorization": self._auth,
                                              "User-Agent": USER_AGENT})
        try:
            return _opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise CseAuthError("Component Search Engine rejected the email/password "
                                   "(HTTP 401). Check them under Settings > Component Search "
                                   "Engine.") from None
            raise CseError(f"Component Search Engine error: HTTP {e.code} {e.reason}") from None
        except urllib.error.URLError as e:
            raise CseError(f"Network error reaching Component Search Engine: {e.reason}") from None
        except TimeoutError:
            raise CseError("Component Search Engine did not answer in time.") from None

    def check_login(self) -> None:
        """Raise CseAuthError if the server rejects the credentials (401). Nothing is saved."""
        with self._open(1):
            pass

    def download(self, part_id: int, dest_dir: Path, progress=None) -> Path:
        """Download the model zip for part_id into dest_dir; progress(done, total|None)."""
        with self._open(part_id) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "html" in ctype or "text" in ctype:
                head = resp.read(2048).decode("utf-8", "replace").strip()
                if head.startswith("Error:") and len(head) < 300:    # e.g. "Error: no part data
                    raise CseError("Component Search Engine: "        # available for partID 1"
                                   + head[len("Error:"):].strip())
                raise CseError("Component Search Engine sent a web page instead of the model. "
                               "Sign in on componentsearchengine.com once in your browser and "
                               "accept any new terms, then retry.")
            total = resp.headers.get("Content-Length")
            total = int(total) if total and total.isdigit() else None
            if total and total > MAX_DOWNLOAD:
                raise CseError("The model download is too large.")
            buf = bytearray()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_DOWNLOAD:
                    raise CseError("The model download is too large.")
                if progress:
                    progress(len(buf), total)
            name = safe_filename(resp.headers.get("Content-Disposition", ""), part_id)
        data = bytes(buf)
        if not data.startswith(b"PK"):
            raise CseError("Component Search Engine did not return a zip file.")
        names = _zip_names(data)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / name
        target.write_bytes(data.rstrip(b"\r\n") if not _is_zip_ok(data) else data)
        if not any(n.lower().endswith((".schlib", ".pcblib", ".intlib", ".libpkg"))
                   for n in names):
            folders = sorted({p for n in names for p in n.strip("/").split("/")[:-1]})
            raise CseError("The downloaded model has no Altium library (folders: "
                           f"{', '.join(folders) or 'none'}). In your Component Search Engine "
                           "account choose Altium Designer as your ECAD tool, then retry. "
                           f"Saved: {target}")
        return target


    def placement(self, part_id: int) -> "StepPlacement | None":
        """3D model placement CSE stores for the part (None if it has no STEP placement)."""
        with self._open(url=PLACEMENT_URL.format(int(part_id))) as resp:
            text = resp.read(MAX_BUILD_SCRIPT).decode("utf-8", "replace")
        if text.lstrip().startswith("Error:"):
            raise CseError("Component Search Engine: " + text.strip()[len("Error:"):].strip()[:200])
        return parse_placement(text)


# ----------------------------------------------------------------------------- 3D placement
# CSE does not store the STEP placement in the zip: the zip's PcbLib has no 3D body and its
# STEP is in the manufacturer's own axes (e.g. a chip resistor modelled lying on its side).
# The placement comes from the part's build script, the text file the SamacSys Library
# Loader for Altium requests (model.php with st=4&lt=4&pi=2; observed in that loader's
# published script, verified live 2026-11-20). One line of it reads
#   AssignSTEPmodel, <file>.stp, <RotX>, <RotY>, <RotZ>, <X mm>, <Y mm>, <Z mm>
# e.g. CRCW08051R00FNEB: 90, 0, 0, 1, 0.63, 0 - without it the model is 90 deg off and
# shifted. Only that line is used; the rest of the script is ignored.
PLACEMENT_URL = f"https://ad.{HOST}/ga/model.php?partID={{}}&st=4&lt=4&pi=2"
MAX_BUILD_SCRIPT = 4 * 1024 * 1024


@dataclass(frozen=True)
class StepPlacement:
    step_name: str
    rot_x: float
    rot_y: float
    rot_z: float
    x_mm: float
    y_mm: float
    z_mm: float

    @property
    def is_identity(self) -> bool:
        return not any((self.rot_x, self.rot_y, self.rot_z, self.x_mm, self.y_mm, self.z_mm))

    def describe(self) -> str:
        return (f"rotation X {self.rot_x:g}, Y {self.rot_y:g}, Z {self.rot_z:g} deg; offset "
                f"X {self.x_mm:g}, Y {self.y_mm:g}, Z {self.z_mm:g} mm")


def parse_placement(text: str) -> StepPlacement | None:
    """The AssignSTEPmodel line of a CSE build script, or None if there is none/it is invalid."""
    import math
    for line in text.replace("\r", "").split("\n"):
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 8 or cells[0] != "AssignSTEPmodel":
            continue
        try:
            nums = [float(c) for c in cells[2:8]]
        except ValueError:
            return None
        if not all(math.isfinite(n) for n in nums) or any(abs(n) > 1000 for n in nums):
            return None                       # mm / degrees: anything larger is not a placement
        return StepPlacement(Path(cells[1]).name, *nums)
    return None


def _is_zip_ok(data: bytes) -> bool:
    try:
        zipfile.ZipFile(io.BytesIO(data)).close()
        return True
    except zipfile.BadZipFile:
        return False
