"""Entry point.

    python -m alm                 start the desktop UI
    python -m alm search <MPN>    command-line search (no UI)
"""

from __future__ import annotations

import sys

from .config import Settings, app_dir
from .credentials import CredentialStore
from .db import Database
from .log import setup_logging
from .models import ProviderState
from .providers.links import MANUAL_SOURCES
from .providers.lcsc import LcscProvider
from .search import run_search


def build_providers(creds: CredentialStore) -> list:
    return [LcscProvider()]


def cli_search(mpn: str, creds: CredentialStore) -> int:
    o = run_search(mpn, build_providers(creds), MANUAL_SOURCES)
    print(f"Query: {o.query}\n")
    for r in o.results:
        print(f"[{r.match.value:<20}] {r.mpn} | {r.manufacturer} | {r.source} | "
              f"symbol {r.symbol.value} footprint {r.footprint.value} 3D {r.model3d.value} | "
              f"datasheet {'yes' if r.datasheet_url else 'no'} | {r.source_url}")
    print("\n" + o.recommendation)
    for w in o.warnings:
        print("WARNING: " + w)
    print("\nSources:")
    for s in o.statuses:
        extra = f" ({s.count})" if s.state is ProviderState.OK else ""
        print(f"  {s.provider:<26} {s.state.value}{extra}  {s.message}  {s.url}")
    return 0 if o.exact_count else 1


def main(argv: list[str]) -> int:
    folder = app_dir()
    setup_logging(folder)
    creds = CredentialStore()
    if len(argv) >= 2 and argv[0] == "search":
        return cli_search(" ".join(argv[1:]), creds)
    if argv:
        print(__doc__)
        return 2
    from .ui.app import run_gui
    run_gui(Settings(), creds, Database(folder / "alm.db"), build_providers(creds))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
