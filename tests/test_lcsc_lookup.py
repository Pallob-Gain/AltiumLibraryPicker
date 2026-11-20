import os
import tempfile
import time
import unittest
from pathlib import Path

from alm import lcsc_lookup as ll
from alm.cse import recent_download


def item(code, model, brand, stock=0, spec="LQFN-56(7x7)"):
    return {"componentCode": code, "componentModelEn": model, "componentBrandEn": brand,
            "componentSpecificationEn": spec, "stockCount": stock}


REPLY = {"code": 200, "data": {"componentPageInfo": {"total": 6, "list": [
    item("C2040", "RP2040", "Raspberry Pi", 69639),
    item("C5374683", "RP2040-LCD-1.28", "Waveshare", 6, "-"),
    item("C2961140", "RP2040", "Raspberry Pi", 0, "QFN-56-EP(7x7)"),
    item("C99", "rp2040 ", "Other Maker", 99999),
    item("bad", "RP2040", "Raspberry Pi"),
    "junk"]}}}


class LookupTests(unittest.TestCase):
    def test_only_exact_mpn_listings(self):
        parts = ll.parse_results(REPLY, "RP2040")
        self.assertEqual([p.code for p in parts], ["C2040", "C2961140", "C99"])
        self.assertEqual(ll.parse_results(REPLY, "MCP23017T-E/ML"), [])

    def test_pick_same_maker_then_stock(self):
        parts = ll.parse_results(REPLY, "RP2040")
        self.assertEqual([p.code for p in ll.pick(parts, "Raspberry Pi Ltd")],
                         ["C2040", "C2961140"])
        self.assertEqual(ll.pick(parts, "")[0].code, "C99")          # no maker: most stock
        self.assertEqual(ll.pick(parts, "Texas Instruments"), [])     # other maker: not used

    def test_same_maker(self):
        self.assertTrue(ll.same_maker("Vishay Dale", "Vishay Intertech"))
        self.assertTrue(ll.same_maker("Analog Devices Inc.", "ADI(Analog Devices)"))
        self.assertTrue(ll.same_maker("Microchip Technology", "Microchip Tech"))
        self.assertTrue(ll.same_maker("ON Semiconductor", "onsemi"))
        self.assertTrue(ll.same_maker("Würth Elektronik", "Wurth Elektronik"))
        self.assertTrue(ll.same_maker("STMicroelectronics", "ST Microelectronics"))
        self.assertFalse(ll.same_maker("ON Semiconductor", "ON Shine"))          # short word
        self.assertFalse(ll.same_maker("Shenzhen Fuman Elec", "Shenzhen Slkor"))  # place name
        self.assertFalse(ll.same_maker("TI", "Texas Instruments"))               # -> chooser
        self.assertFalse(ll.same_maker("Texas Instruments", "Raspberry Pi"))
        self.assertFalse(ll.same_maker("", "Raspberry Pi"))

    def test_error_reply(self):
        with self.assertRaises(ll.LcscError):
            ll.parse_results({"code": 500, "message": "busy"}, "RP2040")
        with self.assertRaises(ll.LcscError):
            ll.parse_results({"code": 200, "data": []}, "RP2040")


class RecentDownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.now = time.time()

    def make(self, name, age, data=b"PK\x03\x04data"):
        p = self.dir / name
        p.write_bytes(data)
        os.utime(p, (self.now - age, self.now - age))
        return p

    def find(self, mpn="RP2040"):
        return recent_download(self.dir, mpn, 1800, clock=lambda: self.now)

    def test_mpn_named_file_wins_over_newer(self):
        self.make("ul_RP2040.zip", 600)
        self.make("other.zip", 60)
        st = self.find()
        self.assertEqual((st.path.name, st.matches_mpn), ("ul_RP2040.zip", True))

    def test_newest_when_no_name_matches(self):
        self.make("a.zip", 600)
        self.make("b.zip", 60)
        st = self.find()
        self.assertEqual((st.path.name, st.matches_mpn), ("b.zip", False))

    def test_ignores_old_partial_empty_and_other_types(self):
        self.make("old_RP2040.zip", 4000)
        self.make("RP2040.pdf", 10)
        self.make("empty.zip", 10, b"")
        self.make("busy.zip", 10)
        self.make("busy.zip.crdownload", 5)
        self.assertIsNone(self.find())

    def test_skips_downloads_already_used(self):
        from alm.cse import file_key
        used = self.make("ul_RP2040.zip", 120)
        self.make("other.zip", 600)
        st = recent_download(self.dir, "RP2040", 1800, clock=lambda: self.now,
                             used={file_key(used)})
        self.assertEqual(st.path.name, "other.zip")
        self.assertIsNone(recent_download(self.dir, "RP2040", 1800, clock=lambda: self.now,
                                          used={file_key(used), file_key(self.dir / "other.zip")}))

    def test_missing_folder(self):
        self.assertIsNone(recent_download(self.dir / "nope", "X", 1800))


if __name__ == "__main__":
    unittest.main()
