"""LCSC part number (C-number) for a manufacturer part number.

Uses the public parts search of JLCPCB (LCSC's assembly service; same C-numbers). It is an
undocumented web service, so a failure is reported as a note and never stops the search.
Only listings whose MPN is EXACTLY the query (ranking.normalize_mpn) are used.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from .ranking import normalize_mpn

SEARCH_URL = ("https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/"
              "selectSmtComponentList")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/131.0.0.0 Safari/537.36")
MAX_REPLY = 2_000_000
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


def parse_results(reply: dict, mpn: str) -> list[LcscPart]:
    """Exact-MPN listings from a JLCPCB search reply."""
    if reply.get("code") != 200:
        raise LcscError(f"JLCPCB parts search answered: {reply.get('message') or reply.get('code')}")
    data = reply.get("data")
    page = data.get("componentPageInfo") if isinstance(data, dict) else None
    items = page.get("list") if isinstance(page, dict) else None
    if items is None:
        items = []
    if not isinstance(items, list) or not isinstance(page, dict):
        raise LcscError("JLCPCB parts search sent an unexpected reply.")
    want = normalize_mpn(mpn)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("componentCode") or "").strip()
        model = str(it.get("componentModelEn") or "")
        if not _CODE.match(code) or not want or normalize_mpn(model) != want:
            continue
        try:
            stock = int(it.get("stockCount") or 0)
        except (TypeError, ValueError):
            stock = 0
        spec = str(it.get("componentSpecificationEn") or "")
        out.append(LcscPart(code, model, str(it.get("componentBrandEn") or ""),
                            "" if spec == "-" else spec, stock))
    return out


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (name or "").lower())


def same_maker(a: str, b: str) -> bool:
    """'Vishay Dale' = 'Vishay Intertech', 'Analog Devices Inc.' = 'ADI(Analog Devices)':
    the first word of either name appears among the other's words."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and (wa[0] in wb or wb[0] in wa)


def pick(parts: list[LcscPart], manufacturer: str = "") -> list[LcscPart]:
    """Listings for the part, best first (most stock). With a manufacturer, only listings of
    the same manufacturer count - an exact MPN from another maker is a different part."""
    if _words(manufacturer):
        parts = [p for p in parts if same_maker(p.manufacturer, manufacturer)]
    return sorted(parts, key=lambda p: p.stock, reverse=True)


def search(mpn: str, timeout: float = 15) -> list[LcscPart]:
    body = json.dumps({"keyword": mpn.strip(), "currentPage": 1, "pageSize": 25}).encode()
    req = urllib.request.Request(SEARCH_URL, data=body, headers={
        "User-Agent": BROWSER_UA, "Content-Type": "application/json",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_REPLY + 1)
    except OSError as e:
        raise LcscError(f"JLCPCB parts search is not reachable ({e}).") from None
    if len(raw) > MAX_REPLY:
        raise LcscError("JLCPCB parts search reply is too large.")
    try:
        reply = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise LcscError("JLCPCB parts search sent an unexpected reply.") from None
    if not isinstance(reply, dict):
        raise LcscError("JLCPCB parts search sent an unexpected reply.")
    return parse_results(reply, mpn)
