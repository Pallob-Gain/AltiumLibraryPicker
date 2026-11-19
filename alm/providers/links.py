"""Manual-assist sources: no supported API, so we only open the right search page.

These are NEVER reported as "searched" (requirement G-5).
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class ManualSource:
    key: str
    name: str
    url_template: str      # "{q}" is replaced by the URL-encoded MPN
    note: str = ""

    def url(self, mpn: str) -> str:
        return self.url_template.format(q=urllib.parse.quote(mpn.strip(), safe=""))


MANUAL_SOURCES: list[ManualSource] = [
    ManualSource("octopart", "Octopart", "https://octopart.com/search?q={q}",
                 "Same data as Altium Manufacturer Part Search; shows CAD model badges."),
    ManualSource("snapmagic", "SnapMagic", "https://www.snapeda.com/search/?q={q}",
                 "Login required to download; Altium format available."),
    ManualSource("ultralibrarian", "Ultra Librarian",
                 "https://app.ultralibrarian.com/search?queryText={q}",
                 "Login required; native Altium export."),
    ManualSource("cse", "Component Search Engine", "https://componentsearchengine.com/search?term={q}",
                 "Login required; also reachable via Library Loader."),
    ManualSource("lcsc", "LCSC", "https://www.lcsc.com/search?q={q}",
                 "Find the C-number for the 'LCSC Part #' parameter."),
    ManualSource("jlcpcb", "JLCPCB / EasyEDA", "https://jlcpcb.com/parts/componentSearch?searchTxt={q}",
                 "EasyEDA models for LCSC parts."),
    ManualSource("mouser_web", "Mouser (web)", "https://www.mouser.com/c/?q={q}"),
    ManualSource("digikey_web", "DigiKey (web)",
                 "https://www.digikey.com/en/products/result?keywords={q}"),
]

ALTIUM_MPS_HELP = (
    "Altium Manufacturer Part Search has no external API.\n\n"
    "The MPN has been copied to the clipboard. In Altium Designer open "
    "View > Panels > Manufacturer Part Search and paste it."
)
