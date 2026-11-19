"""The vendored EasyEDA converter must reproduce EasyEDA-Importer's own expected outputs."""
import json
import tempfile
import unittest
from pathlib import Path

from alm.easyeda import convert as ee

FIX = Path(__file__).parent / "fixtures" / "easyeda"


def _same(exp: str, got: str) -> bool:
    """Equal, tolerating +-1 cmil rounding in numeric fields (same rule as ee_fetch.ps1 -SelfTest)."""
    if exp == got:
        return True
    ea, ga = exp.split("\t"), got.split("\t")
    if len(ea) != len(ga):
        return False
    for e, g in zip(ea, ga):
        if e != g:
            try:
                if abs(int(e) - int(g)) > 1:
                    return False
            except ValueError:
                return False
    return True


class ConverterTests(unittest.TestCase):
    def test_fixtures_match_expected(self):
        files = sorted(FIX.glob("*.raweasy.json"))
        self.assertGreaterEqual(len(files), 6)
        for fx in files:
            with self.subTest(fx.name):
                data = json.loads(fx.read_text(encoding="utf-8"))
                res = data.get("result", data)
                got = ee.to_text(ee.finalize_model(ee.convert(res, ""), None)).rstrip("\n").split("\n")
                exp = (FIX / fx.name.replace(".raweasy.json", ".expected.txt")).read_text(
                    encoding="utf-8-sig").replace("\r", "").rstrip("\n").split("\n")
                self.assertEqual(len(exp), len(got))
                for k, (e, g) in enumerate(zip(exp, got)):
                    self.assertTrue(_same(e, g), f"line {k + 1}: expected {e!r} got {g!r}")

    def test_rejects_non_lcsc_input(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ("../x", "C12/../../a", "ABC", ""):
                with self.assertRaises(ValueError):
                    ee.fetch_and_convert(bad, d)


if __name__ == "__main__":
    unittest.main()
