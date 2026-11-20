import unittest

from alm.models import Avail, Match, ProviderState, ProviderStatus, SearchResult
from alm.ranking import classify, normalize_mpn, rank, recommend, warnings


def res(mpn, mfr="Maker", kind="api", **kw):
    return SearchResult(query="q", mpn=mpn, manufacturer=mfr, source="Src", source_kind=kind, **kw)


OK = [ProviderStatus("LCSC", ProviderState.OK, 1)]


class NormalizeTests(unittest.TestCase):
    def test_case_and_whitespace_only(self):
        self.assertEqual(normalize_mpn(" stm32f103c8t6 "), "STM32F103C8T6")
        self.assertEqual(normalize_mpn("MCP23017T-E / ML"), "MCP23017T-E/ML")

    def test_punctuation_and_suffix_are_significant(self):
        self.assertIs(classify("MCP23017T-E/ML", "MCP23017-E/ML"), Match.SIMILAR)
        self.assertIs(classify("TPA3116D2DADR", "TPA3116D2DAD"), Match.SIMILAR)
        self.assertIs(classify("ESP32-S3-WROOM-1-N8R2", "ESP32S3WROOM1N8R2"), Match.SIMILAR)
        self.assertIs(classify("stdrive141tr", "STDRIVE141TR"), Match.EXACT)


class RankTests(unittest.TestCase):
    def test_exact_always_above_similar(self):
        similar = res("STM32F103C8T6TR", altium_native=Avail.YES, symbol=Avail.YES,
                      footprint=Avail.YES, model3d=Avail.YES, datasheet_url="https://x")
        exact = res("STM32F103C8T6")
        ranked = rank("STM32F103C8T6", [similar, exact])
        self.assertIs(ranked[0], exact)
        self.assertIs(ranked[0].match, Match.EXACT)

    def test_unknown_is_not_scored_as_yes(self):
        a = res("X1", symbol=Avail.UNKNOWN)
        b = res("X1", symbol=Avail.YES)
        ranked = rank("X1", [a, b])
        self.assertIs(ranked[0], b)


class RecommendTests(unittest.TestCase):
    def test_no_provider_searched(self):
        best, text = recommend("X", [], [ProviderStatus("LCSC", ProviderState.NOT_CONFIGURED)])
        self.assertIsNone(best)
        self.assertIn("No provider was searched", text)

    def test_no_exact_match(self):
        ranked = rank("ABC123", [res("ABC1234")])
        best, text = recommend("ABC123", ranked, OK)
        self.assertIsNone(best)
        self.assertIn("No exact match", text)

    def test_exact_recommendation_points_to_cad_buttons(self):
        ranked = rank("ABC", [res("abc", datasheet_url="https://d")])
        best, text = recommend("ABC", ranked, OK)
        self.assertIsNotNone(best)
        self.assertIn("exact MPN match", text)
        self.assertIn("Get from CSE", text)

    def test_ambiguous_manufacturer_and_similar_warnings(self):
        ranked = rank("ABC", [res("ABC", "Alpha"), res("ABC", "Beta"), res("ABC-TR", "Alpha")])
        w = " ".join(warnings("ABC", ranked))
        self.assertIn("Manufacturer ambiguous", w)
        self.assertIn("ABC-TR", w)


if __name__ == "__main__":
    unittest.main()
