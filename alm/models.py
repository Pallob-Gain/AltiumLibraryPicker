"""Core data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Avail(str, Enum):
    """Tri-state availability. UNKNOWN must never be displayed or scored as YES."""
    YES = "Yes"
    NO = "No"
    UNKNOWN = "?"


class Match(str, Enum):
    EXACT = "Exact"
    SIMILAR = "Similar - NOT exact"


class ProviderState(str, Enum):
    OK = "Searched"
    NOT_CONFIGURED = "Not configured"
    ERROR = "Error"
    MANUAL = "Manual - not searched"


@dataclass
class SearchResult:
    query: str
    mpn: str
    manufacturer: str
    source: str                     # display name of the provider
    source_kind: str                # "api" | "package" | "manual"
    description: str = ""
    symbol: Avail = Avail.UNKNOWN
    footprint: Avail = Avail.UNKNOWN
    model3d: Avail = Avail.UNKNOWN
    altium_native: Avail = Avail.UNKNOWN
    download: Avail = Avail.UNKNOWN
    lcsc: str = ""                  # only ever filled from a verified source
    datasheet_url: str = ""
    source_url: str = ""
    supplier: str = ""
    supplier_pn: str = ""
    package: str = ""
    stock: int | None = None        # distributor stock, None = not reported
    match: Match = Match.SIMILAR    # set by ranking
    score: int = 0                  # set by ranking
    notes: list[str] = field(default_factory=list)


@dataclass
class ProviderStatus:
    provider: str
    state: ProviderState
    count: int = 0
    message: str = ""
    url: str = ""
