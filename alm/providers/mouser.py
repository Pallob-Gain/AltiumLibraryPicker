"""Mouser Search API v1 (official). Get a key at https://www.mouser.com/api-search/ .

Endpoint: POST https://api.mouser.com/api/v1/search/partnumber?apiKey=<key>
Body:     {"SearchByPartRequest": {"mouserPartNumber": "<MPN>", "partSearchOptions": "Exact"}}
Mouser does NOT report CAD model availability, so symbol/footprint/3D stay Unknown.
"""

from __future__ import annotations

import urllib.parse

from ..models import SearchResult
from ..ranking import normalize_mpn
from .base import NotConfigured, Provider, ProviderError, http_json

API_URL = "https://api.mouser.com/api/v1/search/partnumber"
CRED_NAME = "mouser_api_key"


class MouserProvider(Provider):
    key = "mouser"
    name = "Mouser API"
    help_url = "https://www.mouser.com/api-search/"

    def __init__(self, credentials):
        self.credentials = credentials

    def configured(self) -> bool:
        return bool(self.credentials.get(CRED_NAME))

    def search(self, mpn: str) -> list[SearchResult]:
        api_key = self.credentials.get(CRED_NAME)
        if not api_key:
            raise NotConfigured("No Mouser API key")
        url = f"{API_URL}?apiKey={urllib.parse.quote(api_key, safe='')}"
        payload = {"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "Exact"}}
        return parse_response(mpn, http_json(url, payload))


def parse_response(query: str, data: dict) -> list[SearchResult]:
    errors = data.get("Errors") or []
    if errors:
        msgs = [str(e.get("Message") or e.get("Code") or e) if isinstance(e, dict) else str(e)
                for e in errors]
        raise ProviderError("Mouser: " + "; ".join(msgs))
    parts = (data.get("SearchResults") or {}).get("Parts") or []
    merged: dict[tuple[str, str], SearchResult] = {}
    for p in parts:
        if not isinstance(p, dict):
            continue
        mpn = (p.get("ManufacturerPartNumber") or "").strip()
        if not mpn:
            continue
        maker = (p.get("Manufacturer") or "").strip()
        mouser_pn = (p.get("MouserPartNumber") or "").strip()
        k = (normalize_mpn(mpn), maker.lower())
        if k in merged:   # same part sold under several Mouser numbers (reel, cut tape...)
            if mouser_pn and mouser_pn not in merged[k].supplier_pn:
                merged[k].supplier_pn += f", {mouser_pn}"
            continue
        r = SearchResult(
            query=query, mpn=mpn, manufacturer=maker, source="Mouser", source_kind="api",
            description=(p.get("Description") or "").strip(),
            datasheet_url=(p.get("DataSheetUrl") or "").strip(),
            source_url=(p.get("ProductDetailUrl") or "").strip(),
            supplier="Mouser", supplier_pn=mouser_pn,
        )
        lifecycle = (p.get("LifecycleStatus") or "").strip()
        if lifecycle:
            r.notes.append(f"Lifecycle: {lifecycle}")
        r.notes.append("Mouser does not report CAD model availability.")
        merged[k] = r
    return list(merged.values())
