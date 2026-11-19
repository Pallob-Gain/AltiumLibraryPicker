"""Runs all providers for one MPN and records honestly what was and wasn't searched."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .models import Match, ProviderState, ProviderStatus, SearchResult
from .providers.base import NotConfigured, Provider, ProviderError
from .providers.links import ManualSource
from .ranking import rank, recommend, warnings

log = logging.getLogger(__name__)


@dataclass
class SearchOutcome:
    query: str
    results: list[SearchResult]
    statuses: list[ProviderStatus]
    best: SearchResult | None
    recommendation: str
    warnings: list[str] = field(default_factory=list)

    @property
    def exact_count(self) -> int:
        return sum(1 for r in self.results if r.match is Match.EXACT)


def run_search(mpn: str, providers: list[Provider],
               manual_sources: list[ManualSource]) -> SearchOutcome:
    query = mpn.strip()
    if not query:
        raise ValueError("Enter a manufacturer part number.")
    results: list[SearchResult] = []
    statuses: list[ProviderStatus] = []
    for p in providers:
        if not p.configured():
            statuses.append(ProviderStatus(p.name, ProviderState.NOT_CONFIGURED,
                                           message="No API key (Settings > API keys)",
                                           url=p.help_url))
            continue
        try:
            found = p.search(query)
        except NotConfigured as e:
            statuses.append(ProviderStatus(p.name, ProviderState.NOT_CONFIGURED, message=str(e)))
        except ProviderError as e:
            log.warning("%s failed: %s", p.name, e)
            statuses.append(ProviderStatus(p.name, ProviderState.ERROR, message=str(e)))
        except Exception as e:  # a provider bug must not kill the search
            log.exception("%s crashed", p.name)
            statuses.append(ProviderStatus(p.name, ProviderState.ERROR,
                                           message=f"Unexpected error: {type(e).__name__}"))
        else:
            results.extend(found)
            statuses.append(ProviderStatus(p.name, ProviderState.OK, count=len(found)))
    for m in manual_sources:
        statuses.append(ProviderStatus(m.name, ProviderState.MANUAL, message=m.note,
                                       url=m.url(query)))
    ranked = rank(query, results)
    best, text = recommend(query, ranked, statuses)
    return SearchOutcome(query, ranked, statuses, best, text, warnings(query, ranked))
