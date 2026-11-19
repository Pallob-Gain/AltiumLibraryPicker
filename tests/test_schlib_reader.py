import tempfile
import unittest
from pathlib import Path

from alm.schlib_reader import NotAnAltiumLibrary, best_match, list_symbols

FIXTURE = Path(__file__).parent / "fixtures" / "ThreeSymbols.SchLib"   # saved by Altium AD26


class SchLibReaderTests(unittest.TestCase):
    def test_lists_symbols_and_footprints(self):
        syms = {s.name: s.footprints for s in list_symbols(FIXTURE)}
        self.assertEqual(syms, {"RT-C06-A1B-AC_C18221846": ["AC-IN-TH_RT-C06-A1B-AC"],
                                "STDRIVE141TR": ["VFQFN32-5x5_STM"],
                                "STDRIVE141TR_1": ["VFQFN32-5x5_STM_1"]})

    def test_rejects_non_ole_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.SchLib"
            p.write_bytes(b"not a library")
            with self.assertRaises(NotAnAltiumLibrary):
                list_symbols(p)

    def test_best_match(self):
        names = ["RT-C06-A1B-AC_C18221846", "STDRIVE141TR", "STDRIVE141TR_1"]
        self.assertEqual(best_match(names, "stdrive141tr"), "STDRIVE141TR")       # exact wins
        self.assertEqual(best_match(names, "", "C18221846"), "RT-C06-A1B-AC_C18221846")
        self.assertEqual(best_match(names, "RT-C06-A1B-AC"), "RT-C06-A1B-AC_C18221846")
        self.assertEqual(best_match(names, "RT-C06"), "")                         # no partial
        self.assertEqual(best_match(["A_1", "A_2"], "A"), "")                     # ambiguous


if __name__ == "__main__":
    unittest.main()
