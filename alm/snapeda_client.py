"""SnapEDA (SnapMagic Search) search + Altium model download - original implementation.

Protocol (learned from the GPL-3.0 project akiselev/datasheet-cli - no code was copied - and
verified live 2026-11-20 with Python's standard library):
  1. GET https://www.snapeda.com/account/login/  -> csrftoken cookie + csrfmiddlewaretoken.
  2. Search (no login): POST /api/v1/search_local_internal, form "q=<mpn>", headers
     X-CSRFToken + X-Requested-With: XMLHttpRequest. JSON {"results": [{unipart_id,
     part_number, manufacturer, has_symbol, has_footprint, has_3d, package_type, ...}]}.
  3. Part details (no login): GET /api/get_part_for_unipart/<unipart_id> -> JSON with
     "download_url": "/parts/snapapi/download-component/<part_id>/<unipart_id>/%s".
  4. Login: POST /account/login/?next=/home/ (Django form: csrfmiddlewaretoken, username,
     password). Success = HTTP 302 away from the login page with a new sessionid cookie.
  5. Download (login needed; without it: 302 to the login page, observed): GET the download
     path with format "altium_native" -> JSON {"url": "<file URL>"} or {"error": "..."}; the
     file (a zip) is then fetched from that URL WITHOUT the SnapEDA cookies.
The user stated (2026-11-20) that they have permission to download from SnapEDA this way.
Requests use an ordinary desktop-browser User-Agent (SnapEDA is behind Cloudflare).

Security: the password is sent only to https://www.snapeda.com in the login form, never logged
and never in error messages. Redirects are never followed automatically. The session lives only
in memory. Downloads are https-only, size-limited and must be zip files.
"""

from __future__ import annotations

import http.cookiejar
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

HOST = "www.snapeda.com"
BASE = f"https://{HOST}"
LOGIN_PATH = "/account/login/"
HOME_PATH = "/home/"
SEARCH_URL = f"{BASE}/api/v1/search_local_internal"
PART_URL = f"{BASE}/api/get_part_for_unipart/{{}}"
ALTIUM_FORMAT = "altium_native"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
MAX_DOWNLOAD = 150 * 1024 * 1024
MAX_PAGE = 5 * 1024 * 1024
CRED_USER, CRED_PASSWORD = "snapeda_user", "snapeda_password"
CRED_SESSION = "snapeda_session"     # signed-in session cookie, DPAPI-encrypted like the password
SNAPEDA_HOME = BASE + "/"
LIB_EXTS = (".schlib", ".pcblib", ".intlib", ".libpkg")
_DOWNLOAD_PATH = re.compile(r"^/parts/snapapi/download-component/\d+/\d+/%s$")


class SnapError(Exception):
    """Shown to the user; never contains the password."""


class SnapAuthError(SnapError):
    pass


@dataclass
class SnapPart:
    unipart_id: int
    mpn: str
    manufacturer: str = ""
    description: str = ""
    package: str = ""
    has_symbol: bool = False
    has_footprint: bool = False
    has_3d: bool = False

    @property
    def model_available(self) -> bool:
        return self.has_symbol and self.has_footprint


def _flag(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def parse_search(data) -> list[SnapPart]:
    if not isinstance(data, dict) or "results" not in data:
        raise SnapError("SnapEDA returned an unexpected search response.")
    out = []
    for r in data.get("results") or []:
        if not isinstance(r, dict):
            continue
        uid = _s(r.get("unipart_id"))
        if not uid.isdecimal():
            continue
        out.append(SnapPart(
            unipart_id=int(uid), mpn=_s(r.get("part_number")),
            manufacturer=_s(r.get("manufacturer")),
            description=_s(r.get("short_description") or r.get("description")),
            package=_s(r.get("package_type")), has_symbol=_flag(r.get("has_symbol")),
            has_footprint=_flag(r.get("has_footprint")), has_3d=_flag(r.get("has_3d"))))
    return out


def exact_matches(parts: list[SnapPart], mpn: str) -> list[SnapPart]:
    """Same MPN (case/whitespace-insensitive only - suffixes matter) with symbol + footprint."""
    from .ranking import normalize_mpn
    want = normalize_mpn(mpn)
    return [p for p in parts if normalize_mpn(p.mpn) == want and p.model_available]


def download_path(details) -> str:
    """The validated download path template from get_part_for_unipart JSON."""
    path = _s(details.get("download_url")) if isinstance(details, dict) else ""
    if not _DOWNLOAD_PATH.match(path):
        raise SnapError("SnapEDA has no downloadable model for this part.")
    return path


def form_token(html: str) -> str:
    m = re.search(r"name=['\"]csrfmiddlewaretoken['\"]\s+value=['\"]([A-Za-z0-9]+)['\"]", html)
    return m.group(1) if m else ""


def safe_filename(url: str, unipart_id: int) -> str:
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    if not name.lower().endswith(".zip") or len(name) > 120:
        name = f"SnapEDA_{unipart_id}.zip"
    return name


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"      # Altium SchLib/PcbLib/IntLib container


def library_filename(url: str, unipart_id: int) -> str:
    """Safe local name for a single Altium library file named in the URL ('' if not one)."""
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    ext = Path(name).suffix.lower()
    if ext not in (".intlib", ".schlib", ".pcblib"):
        return ""
    return name if len(name) <= 120 else f"SnapEDA_{unipart_id}{Path(name).suffix}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                    # surfaced as HTTPError 3xx; handled by the caller


def _plain_opener():
    return urllib.request.build_opener(_NoRedirect)


class SnapClient:
    """One signed-in (or anonymous) SnapEDA session, kept in memory only."""

    def __init__(self, username: str = "", password: str = "", timeout: float = 60,
                 session: str = ""):
        self._user, self._pw = username, password
        self.timeout = timeout
        self._jar = http.cookiejar.CookieJar()
        self._op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar),
                                               _NoRedirect)
        self._logged_in = False
        self._form_token = ""          # login form token from the last login page
        if re.fullmatch(r"[A-Za-z0-9]{8,128}", session or ""):
            self._set_cookie("sessionid", session)   # reuse a saved session: SnapEDA
            self._logged_in = True                     # rate-limits sign-ins (HTTP 429)

    @property
    def session(self) -> str:
        """The signed-in session cookie (to save encrypted), '' if not signed in."""
        return self._cookie("sessionid") if self._logged_in else ""

    def _set_cookie(self, name: str, value: str) -> None:
        self._jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, HOST, False, False, "/", True, True, None, False,
            None, None, {}))

    # ---- HTTP helpers
    def _cookie(self, name: str) -> str:
        return next((c.value for c in self._jar if c.name == name and HOST.endswith(
            c.domain.lstrip("."))), "")

    def _request(self, url: str, data: dict | None = None, headers: dict | None = None,
                 allow_redirect: bool = False):
        """(status, headers, body bytes). A redirect is returned, never followed."""
        if not url.startswith(BASE + "/"):
            raise SnapError("Refusing to send the SnapEDA session to another site.")
        h = {"User-Agent": BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
        h.update(headers or {})
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=h)
        try:
            with self._op.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers, resp.read(MAX_PAGE)
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:
                return e.code, e.headers, b""
            if e.code == 429:
                raise SnapError("SnapEDA says too many requests (HTTP 429). Wait a few minutes, "
                                "then try again.") from None
            if e.code in (403, 503):
                raise SnapError(f"SnapEDA refused the request (HTTP {e.code}) - its bot "
                                "protection may be blocking the app. Wait a few minutes, or use "
                                "the browser: open the part on SnapEDA and download it.") from None
            raise SnapError(f"SnapEDA error: HTTP {e.code} {e.reason}") from None
        except urllib.error.URLError as e:
            raise SnapError(f"Network error reaching SnapEDA: {e.reason}") from None
        except TimeoutError:
            raise SnapError("SnapEDA did not answer in time.") from None

    def _csrf(self) -> str:
        tok = self._cookie("csrftoken")
        if not tok:
            self._login_page()
            tok = self._cookie("csrftoken")
        if not tok:
            raise SnapError("SnapEDA did not provide a session token.")
        return tok

    def _login_page(self) -> str:
        html_accept = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        status, _, body = self._request(BASE + LOGIN_PATH, headers=html_accept)
        if 300 <= status < 400 and self._logged_in:
            # restored session: SnapEDA sends a signed-in user from the login page to /home/
            # (301, verified 2026-11-20); that page sets the csrftoken cookie too
            status, _, body = self._request(BASE + HOME_PATH, headers=html_accept)
        if status != 200:
            raise SnapError(f"SnapEDA login page answered HTTP {status}.")
        html = body.decode("utf-8", "replace")
        self._form_token = form_token(html)
        return html

    def _json(self, body: bytes, what: str):
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except ValueError:
            raise SnapError(f"SnapEDA {what} did not return data.") from None

    # ---- API
    def search(self, mpn: str) -> list[SnapPart]:
        csrf = self._csrf()
        status, _, body = self._request(SEARCH_URL, data={"q": mpn.strip()}, headers={
            "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + "/search/", "Accept": "application/json, */*; q=0.01"})
        if status != 200:
            raise SnapError(f"SnapEDA search answered HTTP {status}.")
        return parse_search(self._json(body, "search"))

    def login(self) -> None:
        """Sign in with the stored username/password. Raises SnapAuthError if rejected."""
        if not (self._user and self._pw):
            raise SnapAuthError("Not signed in: enter your SnapEDA username and password under "
                                "Settings > SnapEDA.")
        if not (self._form_token and self._cookie("csrftoken")):
            self._login_page()            # one page load per session (SnapEDA rate-limits it)
        token, csrf = self._form_token, self._cookie("csrftoken")
        self._form_token = ""             # single use
        if not (token and csrf):
            raise SnapError("SnapEDA's login page has changed; the app cannot sign in.")
        before = self._cookie("sessionid")
        time.sleep(0.5)
        status, headers, _ = self._request(
            BASE + LOGIN_PATH + "?next=/home/",
            data={"csrfmiddlewaretoken": token, "next": "/home/", "username": self._user,
                  "password": self._pw, "plugin": "None", "ref_email": ""},
            headers={"Referer": BASE + LOGIN_PATH, "Origin": BASE,
                     "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        location = (headers.get("Location") or "") if headers else ""
        session = self._cookie("sessionid")
        if status in (301, 302, 303) and LOGIN_PATH not in location and session \
                and session != before:
            self._logged_in = True
            return
        if status == 200:
            raise SnapAuthError("SnapEDA rejected the username/password. Check them under "
                                "Settings > SnapEDA.")
        raise SnapError(f"SnapEDA sign-in failed (HTTP {status}).")

    def part_download_path(self, unipart_id: int) -> str:
        status, _, body = self._request(PART_URL.format(int(unipart_id)),
                                        headers={"Accept": "application/json"})
        if status != 200:
            raise SnapError(f"SnapEDA part details answered HTTP {status}.")
        return download_path(self._json(body, "part details"))

    def file_url(self, unipart_id: int, fmt: str = ALTIUM_FORMAT) -> str:
        """Signed-in: the URL of the model file in the given format."""
        path = self.part_download_path(unipart_id) % fmt
        for attempt in (1, 2):
            if not self._logged_in:
                self.login()
            time.sleep(0.5)
            status, headers, body = self._request(BASE + path, headers={
                "Referer": BASE + "/", "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, */*; q=0.01"})
            if status in (301, 302, 303) and LOGIN_PATH in (headers.get("Location") or ""):
                self._logged_in = False           # session expired: sign in again once
                self._jar.clear()                 # drop the old session cookies
                self._form_token = ""
                if attempt == 1:
                    continue
                raise SnapAuthError("SnapEDA did not accept the sign-in for downloads.")
            if status != 200:
                raise SnapError(f"SnapEDA download answered HTTP {status}.")
            data = self._json(body, "download")
            if isinstance(data, dict) and data.get("error"):
                raise SnapError("SnapEDA: " + _s(data.get("error"))[:200])
            url = _s(data.get("url")) if isinstance(data, dict) else ""
            if not url.startswith("https://"):
                raise SnapError("SnapEDA did not return a download link.")
            return url
        raise SnapAuthError("SnapEDA sign-in failed.")

    def download(self, part: SnapPart, dest_dir: Path, progress=None) -> Path:
        """Download the Altium model zip of a part into dest_dir; progress(done, total|None)."""
        url = self.file_url(part.unipart_id)
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        try:
            with _plain_opener().open(req, timeout=self.timeout) as resp:   # no SnapEDA cookies
                total = resp.headers.get("Content-Length")
                total = int(total) if total and total.isdigit() else None
                if total and total > MAX_DOWNLOAD:
                    raise SnapError("The model download is too large.")
                buf = bytearray()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > MAX_DOWNLOAD:
                        raise SnapError("The model download is too large.")
                    if progress:
                        progress(len(buf), total)
        except urllib.error.HTTPError as e:
            raise SnapError(f"Downloading the model file failed: HTTP {e.code}") from None
        except urllib.error.URLError as e:
            raise SnapError(f"Network error downloading the model file: {e.reason}") from None
        data = bytes(buf)
        dest_dir.mkdir(parents=True, exist_ok=True)
        if data.startswith(OLE_MAGIC):          # a single Altium library file (e.g. .IntLib)
            name = library_filename(url, part.unipart_id)
            if not name:
                raise SnapError("SnapEDA sent an Altium file of an unknown kind ("
                                + (Path(urllib.parse.urlparse(url).path).name or "no name")[:80]
                                + ").")
            target = dest_dir / name
            target.write_bytes(data)
            return target
        if not data.startswith(b"PK"):
            raise SnapError("SnapEDA did not send an Altium library (neither a zip nor an "
                            "Altium file).")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            raise SnapError("The downloaded zip file is damaged.") from None
        target = dest_dir / safe_filename(url, part.unipart_id)
        target.write_bytes(data)
        if not any(n.lower().endswith(LIB_EXTS) for n in names):
            raise SnapError(f"The SnapEDA download has no Altium library. Saved: {target}")
        return target
