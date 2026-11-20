"""Exact-match classification, scoring and recommendation (requirement F2)."""

from __future__ import annotations

from .models import Avail, Match, ProviderState, ProviderStatus, SearchResult

KIND_SCORE = {"api": 30, "package": 20, "manual": 0}


def normalize_mpn(mpn: str) -> str:
    """Case-insensitive, whitespace-insensitive ONLY.

    Hyphens, slashes and suffixes are significant: MCP23017-E/ML and MCP23017T-E/ML
    are different orderable parts and must never be treated as equal.
    """
    return "".join((mpn or "").split()).upper()


def classify(query: str, mpn: str) -> Match:
    return Match.EXACT if normalize_mpn(query) == normalize_mpn(mpn) else Match.SIMILAR


def score(r: SearchResult) -> int:
    s = 1000 if r.match is Match.EXACT else 0
    s += KIND_SCORE.get(r.source_kind, 0)
    if r.altium_native is Avail.YES:
        s += 20
    s += sum(10 for a in (r.symbol, r.footprint, r.model3d) if a is Avail.YES)
    if r.download is Avail.YES:
        s += 5
    if r.datasheet_url:
        s += 5
    return s


def rank(query: str, results: list[SearchResult]) -> list[SearchResult]:
    for r in results:
        r.match = classify(query, r.mpn)
        r.score = score(r)
    return sorted(results, key=lambda r: (-r.score, r.source, r.mpn))


def warnings(query: str, ranked: list[SearchResult]) -> list[str]:
    out: list[str] = []
    exact = [r for r in ranked if r.match is Match.EXACT]
    similar = [r for r in ranked if r.match is Match.SIMILAR]
    makers = {r.manufacturer.strip().lower() for r in exact if r.manufacturer.strip()}
    if len(makers) > 1:
        names = sorted({r.manufacturer.strip() for r in exact if r.manufacturer.strip()})
        listed = ", ".join(names)
        out.append("Manufacturer ambiguous: the exact MPN is listed by " + listed
                   + ("" if listed.endswith(".") else ".")
                   + " Verify the manufacturer before importing (click a row to use it).")
    if similar:
        variants = sorted({r.mpn for r in similar})
        shown = ", ".join(variants[:8]) + (" ..." if len(variants) > 8 else "")
        out.append(f"{len(variants)} similar part number(s) found ({shown}). These are different "
                   "parts or package/packaging variants and must not be substituted.")
    return out


def recommend(query: str, ranked: list[SearchResult],
              statuses: list[ProviderStatus]) -> tuple[SearchResult | None, str]:
    searched = [s for s in statuses if s.state is ProviderState.OK]
    exact = [r for r in ranked if r.match is Match.EXACT]
    if not searched:
        return None, ("No provider was searched automatically (none configured or all failed). "
                      "Use the manual-assist sources below.")
    if not exact:
        return None, (f"No exact match for '{query}' from: "
                      + ", ".join(s.provider for s in searched)
                      + ". Check the spelling and the full orderable suffix, or use the manual-assist sources.")
    best = exact[0]
    reasons = ["exact MPN match"]
    if best.source_kind == "api":
        reasons.append("distributor listing")
    if best.stock:
        reasons.append(f"{best.stock:,} in stock")
    if best.altium_native is Avail.YES:
        reasons.append("native Altium format")
    have = [n for n, a in (("symbol", best.symbol), ("footprint", best.footprint),
                           ("3D", best.model3d)) if a is Avail.YES]
    if have:
        reasons.append("has " + "/".join(have))
    if best.datasheet_url:
        reasons.append("datasheet available")
    text = (f"Recommended: {best.mpn} by {best.manufacturer or 'unknown manufacturer'} "
            f"via {best.source} - " + ", ".join(reasons) + ".")
    unknown = [n for n, a in (("symbol", best.symbol), ("footprint", best.footprint),
                              ("3D", best.model3d)) if a is Avail.UNKNOWN]
    if unknown:
        text += (" Get the symbol, footprint and 3D model with the buttons in step 3 "
                 "(start with Get from CSE).")
    return best, text
