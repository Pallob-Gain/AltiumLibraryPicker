import unittest
from unittest import mock

from alm.models import Avail, ProviderState
from alm.providers import lcsc
from alm.providers.base import NotConfigured, ProviderError
from alm.providers.links import MANUAL_SOURCES, ManualSource
from alm.search import run_search

# Shape of an LCSC (JLCPCB parts search) reply (values are fixture data).
def item(code, model, brand, stock, **kw):
    d = {"componentCode": code, "componentModelEn": model, "componentBrandEn": brand,
         "stockCount": stock, "componentSpecificationEn": "LQFP-48(7x7)",
         "describe": "ARM  MCU   72MHz", "dataManualUrl": "https://www.lcsc.com/datasheet/x.pdf",
         "lcscGoodsUrl": "https://www.lcsc.com/product-detail/x_C8734.html"}
    d.update(kw)
    return d


LCSC_FIXTURE = {"code": 200, "data": {"componentPageInfo": {"total": 4, "list": [
    item("C8734", "STM32F103C8T6", "STMicroelectronics", 1200),
    item("C77994", "STM32F103C8T6", "STMicroelectronics", 99000,
         dataManualOfficialLink="https://www.st.com/ds.pdf", lcscGoodsUrl="javascript:alert(1)"),
    item("C9999", "STM32F103C8T7", "ADI(Analog Devices)", 5, componentSpecificationEn="-"),
    item("bad", "STM32F103C8T6", "STMicroelectronics", 1)]}}}


class LcscParseTests(unittest.TestCase):
    def test_rows_with_lcsc_number_stock_first(self):
        out = lcsc.parse_response("STM32F103C8T6", LCSC_FIXTURE)
        self.assertEqual([r.lcsc for r in out], ["C77994", "C8734", "C9999"])   # invalid code out
        r = out[0]
        self.assertEqual((r.source, r.supplier_pn, r.stock), ("LCSC", "C77994", 99000))
        self.assertEqual(r.datasheet_url, "https://www.st.com/ds.pdf")   # official link first
        self.assertEqual(r.source_url, "")                               # https only
        self.assertEqual(r.description, "ARM MCU 72MHz")
        self.assertIs(r.symbol, Avail.UNKNOWN)                           # no CAD info
        self.assertEqual((out[2].manufacturer, out[2].package), ("Analog Devices", ""))

    def test_error_reply_raises(self):
        with self.assertRaises(ProviderError):
            lcsc.parse_response("X", {"code": 500, "message": "busy"})

    def test_empty_reply(self):
        self.assertEqual(lcsc.parse_response("X", {"code": 200, "data": {
            "componentPageInfo": {"total": 0, "list": None}}}), [])

    def test_no_key_needed_and_network_error_is_provider_error(self):
        p = lcsc.LcscProvider()
        self.assertTrue(p.configured())
        with mock.patch.object(lcsc.lcsc_lookup, "fetch",
                               side_effect=lcsc.lcsc_lookup.LcscError("not reachable")):
            with self.assertRaises(ProviderError):
                p.search("X")


class LinkTests(unittest.TestCase):
    def test_slash_is_encoded(self):
        src = ManualSource("t", "T", "https://x/search?q={q}")
        self.assertEqual(src.url("MCP23017T-E/ML"), "https://x/search?q=MCP23017T-E%2FML")

    def test_all_templates_https(self):
        for s in MANUAL_SOURCES:
            self.assertTrue(s.url("A B").startswith("https://"), s.key)
            if s.key == "celestial":          # no MPN web search: opens the setup guide
                self.assertNotIn("{q}", s.url_template)
                continue
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
        parts = lcsc.parse_response("STM32F103C8T6", LCSC_FIXTURE)
        o = run_search("stm32f103c8t6", [_Prov(result=parts)], [])
        self.assertEqual(o.exact_count, 2)                 # two LCSC listings of the MPN
        self.assertIs(o.best, parts[0])                    # the one with the most stock
        self.assertEqual(o.statuses[0].count, 3)
        self.assertIn("99,000 in stock", o.recommendation)


if __name__ == "__main__":
    unittest.main()
