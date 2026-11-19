import unittest
from unittest import mock

from alm.models import Avail, ProviderState
from alm.providers import mouser
from alm.providers.base import NotConfigured, ProviderError
from alm.providers.links import MANUAL_SOURCES, ManualSource
from alm.search import run_search

# Shape of a Mouser Search API v1 response (values are fixture data, not real part data).
MOUSER_FIXTURE = {
    "Errors": [],
    "SearchResults": {
        "NumberOfResult": 3,
        "Parts": [
            {"ManufacturerPartNumber": "STM32F103C8T6", "Manufacturer": "STMicroelectronics",
             "Description": "ARM Microcontrollers - MCU", "DataSheetUrl": "https://example.com/ds.pdf",
             "ProductDetailUrl": "https://www.mouser.com/ProductDetail/x", "MouserPartNumber": "511-A",
             "LifecycleStatus": None},
            {"ManufacturerPartNumber": "STM32F103C8T6", "Manufacturer": "STMicroelectronics",
             "MouserPartNumber": "511-B"},
            {"ManufacturerPartNumber": "", "Manufacturer": "junk"},
        ],
    },
}


class FakeCreds:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, name):
        return self.values.get(name, "")


class MouserParseTests(unittest.TestCase):
    def test_parse_merges_duplicate_supplier_numbers(self):
        out = mouser.parse_response("STM32F103C8T6", MOUSER_FIXTURE)
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r.manufacturer, "STMicroelectronics")
        self.assertEqual(r.supplier_pn, "511-A, 511-B")
        self.assertEqual(r.datasheet_url, "https://example.com/ds.pdf")
        self.assertIs(r.symbol, Avail.UNKNOWN)       # Mouser has no CAD info
        self.assertEqual(r.lcsc, "")                  # never invented

    def test_errors_raise(self):
        with self.assertRaises(ProviderError):
            mouser.parse_response("X", {"Errors": [{"Code": "Invalid", "Message": "Bad key"}]})

    def test_empty_response(self):
        self.assertEqual(mouser.parse_response("X", {"Errors": [], "SearchResults": None}), [])

    def test_not_configured(self):
        with self.assertRaises(NotConfigured):
            mouser.MouserProvider(FakeCreds()).search("X")

    def test_request_shape(self):
        p = mouser.MouserProvider(FakeCreds({"mouser_api_key": "k/ey"}))
        with mock.patch.object(mouser, "http_json", return_value=MOUSER_FIXTURE) as m:
            p.search("STM32F103C8T6")
        url, payload = m.call_args.args
        self.assertTrue(url.endswith("?apiKey=k%2Fey"))
        self.assertEqual(payload["SearchByPartRequest"],
                         {"mouserPartNumber": "STM32F103C8T6", "partSearchOptions": "Exact"})


class LinkTests(unittest.TestCase):
    def test_slash_is_encoded(self):
        src = ManualSource("t", "T", "https://x/search?q={q}")
        self.assertEqual(src.url("MCP23017T-E/ML"), "https://x/search?q=MCP23017T-E%2FML")

    def test_all_templates_https(self):
        for s in MANUAL_SOURCES:
            self.assertTrue(s.url("A B").startswith("https://"), s.key)
            self.assertIn("A%20B", s.url("A B"))


class _Prov:
    name = "Fake"
    help_url = ""

    def __init__(self, configured=True, result=None, exc=None):
        self._c, self._r, self._e = configured, result or [], exc

    def configured(self):
        return self._c

    def search(self, mpn):
        if self._e:
            raise self._e
        return self._r


class SearchHonestyTests(unittest.TestCase):
    def test_statuses_are_honest(self):
        o = run_search("STM32F103C8T6", [
            _Prov(configured=False),
            _Prov(exc=ProviderError("HTTP 500")),
            _Prov(exc=KeyError("bug")),
        ], MANUAL_SOURCES)
        states = [s.state for s in o.statuses]
        self.assertEqual(states[:3], [ProviderState.NOT_CONFIGURED, ProviderState.ERROR,
                                      ProviderState.ERROR])
        self.assertTrue(all(s.state is ProviderState.MANUAL for s in o.statuses[3:]))
        self.assertNotIn(ProviderState.OK, states)
        self.assertIsNone(o.best)

    def test_empty_query_rejected(self):
        with self.assertRaises(ValueError):
            run_search("   ", [], [])

    def test_successful_provider(self):
        parts = mouser.parse_response("STM32F103C8T6", MOUSER_FIXTURE)
        o = run_search("stm32f103c8t6", [_Prov(result=parts)], [])
        self.assertEqual(o.exact_count, 1)
        self.assertIs(o.best, parts[0])
        self.assertEqual(o.statuses[0].count, 1)


if __name__ == "__main__":
    unittest.main()
