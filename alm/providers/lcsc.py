"""LCSC parts search - no API key needed (see alm/lcsc_lookup.py; used with LCSC's permission).

Each listing becomes one result row with its LCSC number (C-number), manufacturer, package,
stock, datasheet and LCSC product page. LCSC does not report CAD model availability, so
symbol/footprint/3D stay Unknown.
"""

from __future__ import annotations

from .. import lcsc_lookup
from ..models import SearchResult
from ..ranking import normalize_mpn
from .base import Provider, ProviderError

MAX_SIMILAR = 30          # near-miss rows shown (all exact listings are always kept)


class LcscProvider(Provider):
    key = "lcsc"
    name = "LCSC"
    help_url = "https://www.lcsc.com/"

    def configured(self) -> bool:
        return True                      # no key or login

    def search(self, mpn: str) -> list[SearchResult]:
        try:
            reply = lcsc_lookup.fetch(mpn)
        except lcsc_lookup.LcscError as e:
            raise ProviderError(str(e)) from None
        return parse_response(mpn, reply)


def parse_response(query: str, reply: dict) -> list[SearchResult]:
    try:
        parts = lcsc_lookup.all_parts(reply)
    except lcsc_lookup.LcscError as e:
        raise ProviderError(str(e)) from None
    want = normalize_mpn(query)
    exact = [p for p in parts if normalize_mpn(p.mpn) == want]
    similar = [p for p in parts if normalize_mpn(p.mpn) != want]
    by_stock = lambda p: p.stock                                    # noqa: E731
    out = []
    for p in sorted(exact, key=by_stock, reverse=True) + \
            sorted(similar, key=by_stock, reverse=True)[:MAX_SIMILAR]:
        r = SearchResult(query=query, mpn=p.mpn, manufacturer=p.manufacturer, source="LCSC",
                         source_kind="api", description=p.description,
                         datasheet_url=p.datasheet_url, source_url=p.url, supplier="LCSC",
                         supplier_pn=p.code, package=p.package, lcsc=p.code, stock=p.stock)
        out.append(r)
    return out
