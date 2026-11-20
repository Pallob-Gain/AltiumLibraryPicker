"""LCSC parts search (no key): part listings and LCSC numbers (C-numbers) for an MPN.

Uses the public parts search of JLCPCB (LCSC's assembly service; same C-numbers and the same
catalogue), used with LCSC's permission. It is an undocumented web service, so a failure is
reported as a note and never stops the app. LCSC numbers are only taken from listings whose
MPN is EXACTLY the query (ranking.normalize_mpn).
"""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.request
from dataclasses import dataclass

from .ranking import normalize_mpn

SEARCH_URL = ("https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/"
              "selectSmtComponentList")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
MAX_REPLY = 2_000_000
PAGE_SIZE = 100                 # exact listings can be far down for common MPNs (LM358)
_CODE = re.compile(r"^C\d{1,10}$")


class LcscError(Exception):
    pass


@dataclass
class LcscPart:
    code: str            # "C2040"
    mpn: str
    manufacturer: str
    package: str = ""
    stock: int = 0
    description: str = ""
    datasheet_url: str = ""
    url: str = ""        # LCSC product page


def _https(value) -> str:
    v = str(value or "").strip()
    return v if v.startswith("https://") and " " not in v else ""


def clean_brand(name: str) -> str:
    """'ADI(Analog Devices)' -> 'Analog Devices'; other names unchanged."""
    name = (name or "").strip()
    m = re.match(r"^([^()]{1,6})\(([A-Za-z][^()]+)\)$", name)
    return m.group(2).strip() if m else name


def items(reply: dict) -> list:
    """The listing items of a JLCPCB search reply (LcscError if it is not a valid reply)."""
    if reply.get("code") != 200:
        raise LcscError(f"LCSC parts search answered: {reply.get('message') or reply.get('code')}")
    data = reply.get("data")
    page = data.get("componentPageInfo") if isinstance(data, dict) else None
    found = page.get("list") if isinstance(page, dict) else None
    if found is None:
        found = []
    if not isinstance(found, list) or not isinstance(page, dict):
        raise LcscError("LCSC parts search sent an unexpected reply.")
    return found


def to_part(it) -> LcscPart | None:
    """One listing, or None if it has no valid C-number / MPN."""
    if not isinstance(it, dict):
        return None
    code = str(it.get("componentCode") or "").strip()
    model = str(it.get("componentModelEn") or "").strip()
    if not _CODE.match(code) or not model:
        return None
    try:
        stock = int(it.get("stockCount") or 0)
    except (TypeError, ValueError):
        stock = 0
    spec = str(it.get("componentSpecificationEn") or "").strip()
    return LcscPart(code, model, clean_brand(str(it.get("componentBrandEn") or "")),
                    "" if spec == "-" else spec, stock,
                    " ".join(str(it.get("describe") or "").split())[:300],
                    _https(it.get("dataManualOfficialLink")) or _https(it.get("dataManualUrl")),
                    _https(it.get("lcscGoodsUrl")))


def all_parts(reply: dict) -> list[LcscPart]:
    return [p for p in (to_part(it) for it in items(reply)) if p]


def parse_results(reply: dict, mpn: str) -> list[LcscPart]:
    """Exact-MPN listings from a search reply."""
    want = normalize_mpn(mpn)
    return [p for p in all_parts(reply) if want and normalize_mpn(p.mpn) == want]


def _words(name: str) -> list[str]:
    ascii_name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", ascii_name.lower())


# place names etc. that start many unrelated company names ("Shenzhen Fuman", "Shenzhen Slkor")
_GENERIC = {"shenzhen", "guangdong", "guangzhou", "dongguan", "jiangsu", "zhejiang", "shanghai",
            "beijing", "hangzhou", "wuxi", "suzhou", "xiamen", "china", "the", "international",
            "global"}


def _key_word(words: list[str]) -> str:
    """The distinctive word of a company name: the first one that isn't generic and has at
    least 4 letters ('ON', 'TI', 'ST' alone are too short to identify a company)."""
    for w in words:
        if w not in _GENERIC:
            return w if len(w) >= 4 else ""
    return ""


def same_maker(a: str, b: str) -> bool:
    """True for two spellings of one company:
    'Vishay Dale' = 'Vishay Intertech', 'Microchip Tech' = 'Microchip Technology',
    'ON Semiconductor' = 'onsemi', 'Würth Elektronik' = 'Wurth Elektronik';
    but 'ON Semiconductor' != 'ON Shine' and 'Shenzhen Fuman' != 'Shenzhen Slkor'."""
    wa, wb = _words(a), _words(b)
    if not (wa and wb):
        return False
    ja, jb = "".join(wa), "".join(wb)
    if min(len(ja), len(jb)) >= 3 and (ja.startswith(jb) or jb.startswith(ja)):
        return True
    ka, kb = _key_word(wa), _key_word(wb)
    return bool(ka and ka in wb) or bool(kb and kb in wa)


def pick(parts: list[LcscPart], manufacturer: str = "") -> list[LcscPart]:
    """Listings for the part, best first (most stock). With a manufacturer, only listings of
    the same manufacturer count - an exact MPN from another maker is a different part."""
    if _words(manufacturer):
        parts = [p for p in parts if same_maker(p.manufacturer, manufacturer)]
    return sorted(parts, key=lambda p: p.stock, reverse=True)


def fetch(mpn: str, timeout: float = 15) -> dict:
    """Raw search reply for a keyword."""
    body = json.dumps({"keyword": mpn.strip(), "currentPage": 1,
                       "pageSize": PAGE_SIZE}).encode()
    req = urllib.request.Request(SEARCH_URL, data=body, headers={
        "User-Agent": BROWSER_UA, "Content-Type": "application/json",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_REPLY + 1)
    except OSError as e:
        raise LcscError(f"LCSC parts search is not reachable ({e}).") from None
    if len(raw) > MAX_REPLY:
        raise LcscError("LCSC parts search reply is too large.")
    try:
        reply = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise LcscError("LCSC parts search sent an unexpected reply.") from None
    if not isinstance(reply, dict):
        raise LcscError("LCSC parts search sent an unexpected reply.")
    return reply


def search(mpn: str, timeout: float = 15) -> list[LcscPart]:
    """Exact-MPN listings for an MPN."""
    return parse_results(fetch(mpn, timeout), mpn)
