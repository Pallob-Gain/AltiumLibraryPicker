"""Provider interface and a small HTTPS JSON helper (standard library only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import __version__
from ..models import SearchResult

USER_AGENT = f"AltiumLibraryManager/{__version__}"
MAX_RESPONSE_BYTES = 5_000_000


class ProviderError(Exception):
    """A provider failed. The message is shown to the user and must not contain secrets."""


class NotConfigured(ProviderError):
    pass


class Provider:
    key = ""
    name = ""
    help_url = ""      # where the user obtains credentials

    def configured(self) -> bool:
        raise NotImplementedError

    def search(self, mpn: str) -> list[SearchResult]:
        raise NotImplementedError


def http_json(url: str, payload: dict | None = None, headers: dict | None = None,
              timeout: float = 20.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs,
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:      # never include the URL: it may carry an API key
        raise ProviderError(f"HTTP {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise ProviderError(f"Network error: {e.reason}") from None
    except TimeoutError:
        raise ProviderError("Request timed out") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProviderError("Response too large")
    try:
        result = json.loads(body.decode("utf-8"))
    except ValueError:
        raise ProviderError("Invalid JSON response") from None
    if not isinstance(result, dict):
        raise ProviderError("Unexpected response format")
    return result
