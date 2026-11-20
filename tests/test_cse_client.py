import base64
import json
import io
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from alm import cse_client as cc

EPW = "123456\nmpn=STM32F103C8T6\nmna=STMicroelectronics\nsrc=cse\n"


def make_zip(files: dict[str, bytes]) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


class FakeResp:
    def __init__(self, body: bytes, headers: dict):
        self._b = io.BytesIO(body)
        self.headers = headers

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class EpwTests(unittest.TestCase):
    def test_parse(self):
        e = cc.parse_epw(EPW)
        self.assertEqual((e.part_id, e.mpn, e.manufacturer), (123456, "STM32F103C8T6",
                                                               "STMicroelectronics"))

    def test_parse_rejects_non_epw(self):
        with self.assertRaises(cc.CseError):
            cc.parse_epw("<html>login</html>")

    def test_read_epw_from_file_and_zip(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "a.epw").write_text(EPW)
            self.assertEqual(cc.read_epw(d / "a.epw").part_id, 123456)
            (d / "b.zip").write_bytes(make_zip({"LIB_X/x.epw": EPW.encode()}) + b"\n")
            self.assertEqual(cc.read_epw(d / "b.zip").part_id, 123456)    # trailing \n ok
            (d / "c.zip").write_bytes(make_zip({"x.epw": EPW.encode(),
                                                "X/Altium/X.SchLib": b"x"}))
            self.assertIsNone(cc.read_epw(d / "c.zip"))     # full library download, not an epw
            (d / "d.zip").write_bytes(make_zip({"X/Altium/X.SchLib": b"x"}))
            self.assertIsNone(cc.read_epw(d / "d.zip"))

    def test_safe_filename(self):
        self.assertEqual(cc.safe_filename('attachment; filename="LIB_STM32.zip"', 1),
                         "LIB_STM32.zip")
        self.assertEqual(cc.safe_filename('attachment; filename="..\\..\\evil.zip"', 7),
                         "evil.zip")
        self.assertEqual(cc.safe_filename('attachment; filename="x.exe"', 7), "LIB_7.zip")
        self.assertEqual(cc.safe_filename("", 7), "LIB_7.zip")


SEARCH = {"PartCount": 4, "Status": "OK", "Message": "", "Parts": {
    "12891002": {"PartNumber": "STM32F103C8T6", "Manufacturer": "STMicroelectronics",
                 "ModelAvailable": "1", "Have3D": "1", "Symbol": "https://g/symbol.php?partID=1",
                 "Footprint": "https://g/footprint.php?partID=1", "PartID": "12891002",
                 "Confidence": "C3", "PackageCategory": "QFP", "PinCount": "48"},
    "13252305": {"PartNumber": "STM32F103C8T6XXX", "Manufacturer": "STMicroelectronics",
                 "ModelAvailable": "1", "Symbol": "s", "Footprint": "f", "PartID": "13252305"},
    "pid555": {"PartNumber": "stm32f103c8t6 ", "Manufacturer": "Other",
               "ModelAvailable": "1", "Symbol": "s", "Footprint": "f"},
    "777": {"PartNumber": "STM32F103C8T6", "Manufacturer": "NoModel",
            "ModelAvailable": "0", "Symbol": None, "Footprint": None, "PartID": "777"}}}


class SearchTests(unittest.TestCase):
    def test_parse_and_exact(self):
        parts = cc.parse_search(SEARCH)
        self.assertEqual(len(parts), 4)
        exact = cc.exact_matches(parts, "STM32F103C8T6")
        self.assertEqual(sorted(p.part_id for p in exact), [555, 12891002])   # 'pid' prefix ok
        st = next(p for p in exact if p.part_id == 12891002)
        self.assertTrue(st.has_3d and st.model_available)
        self.assertEqual((st.package, st.pins), ("QFP", "48"))

    def test_suffix_variant_and_no_model_excluded(self):
        names = [p.mpn for p in cc.exact_matches(cc.parse_search(SEARCH), "STM32F103C8T6")]
        self.assertNotIn("STM32F103C8T6XXX", names)
        self.assertEqual(len([p for p in cc.parse_search(SEARCH) if p.manufacturer == "NoModel"
                              and p.model_available]), 0)

    def test_empty_and_bad_shapes(self):
        self.assertEqual(cc.parse_search({"PartCount": 0, "Parts": None}), [])
        with self.assertRaises(cc.CseError):
            cc.parse_search(["not", "a", "dict"])

    def test_search_request(self):
        seen = {}
        client = cc.CseClient("me@example.com", "pw")

        def fake_open(req, timeout):
            seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
            return FakeResp(json.dumps(SEARCH).encode(), {"Content-Type": "application/json"})
        with mock.patch.object(cc._opener, "open", side_effect=fake_open):
            parts = client.search(" STM32F103C8T6 ")
        self.assertEqual(seen["url"], "https://ad.componentsearchengine.com/ga/search.php?"
                                      "kws=STM32F103C8T6&v=1")
        self.assertTrue(seen["auth"].startswith("Basic "))
        self.assertEqual(len(parts), 4)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.client = cc.CseClient("me@example.com", "s3cret pw")

    def tearDown(self):
        self._tmp.cleanup()

    def patch_open(self, result):
        return mock.patch.object(cc._opener, "open", side_effect=[result]
                                 if not callable(result) else result)

    def test_requires_credentials(self):
        with self.assertRaises(cc.CseAuthError):
            cc.CseClient("", "")

    def test_download_sends_basic_auth_and_returns_zip(self):
        body = make_zip({"LIB_X/Altium/X.SchLib": b"s", "LIB_X/Altium/X.PcbLib": b"p",
                         "LIB_X/3D/X.stp": b"3"})
        seen, progress = {}, []

        def fake_open(req, timeout):
            seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
            return FakeResp(body, {"Content-Type": "application/x-zip",
                                   "Content-Length": str(len(body)),
                                   "Content-Disposition": 'attachment; filename="LIB_X.zip"'})
        with mock.patch.object(cc._opener, "open", side_effect=fake_open):
            path = self.client.download(42, self.dir, lambda d, t: progress.append((d, t)))
        self.assertEqual(seen["url"], "https://componentsearchengine.com/ga/model.php?partID=42")
        self.assertEqual(seen["auth"], "Basic " + base64.b64encode(
            b"me@example.com:s3cret pw").decode())
        self.assertEqual(path.name, "LIB_X.zip")
        self.assertEqual(path.read_bytes(), body)
        self.assertEqual(progress[-1], (len(body), len(body)))

    def test_401_is_auth_error_without_password(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with mock.patch.object(cc._opener, "open", side_effect=err):
            with self.assertRaises(cc.CseAuthError) as ctx:
                self.client.download(1, self.dir)
        self.assertNotIn("s3cret", str(ctx.exception))
        with mock.patch.object(cc._opener, "open", side_effect=err):
            with self.assertRaises(cc.CseAuthError):
                self.client.check_login()

    def test_html_page_is_reported(self):
        with mock.patch.object(cc._opener, "open", return_value=FakeResp(
                b"<html>terms</html>", {"Content-Type": "text/html"})):
            with self.assertRaises(cc.CseError) as ctx:
                self.client.download(1, self.dir)
        self.assertIn("accept", str(ctx.exception))

    def test_server_error_text_is_shown(self):     # observed live for an unknown part ID
        with mock.patch.object(cc._opener, "open", return_value=FakeResp(
                b"Error: no part data available for partID 1\n", {"Content-Type": "text/html"})):
            with self.assertRaises(cc.CseError) as ctx:
                self.client.download(1, self.dir)
        self.assertIn("no part data available for partID 1", str(ctx.exception))

    def test_zip_without_altium_lists_folders(self):
        body = make_zip({"LIB_X/KiCad/X.kicad_sym": b"k", "LIB_X/3D/X.stp": b"3"})
        with mock.patch.object(cc._opener, "open", return_value=FakeResp(
                body, {"Content-Type": "application/x-zip"})):
            with self.assertRaises(cc.CseError) as ctx:
                self.client.download(5, self.dir)
        self.assertIn("KiCad", str(ctx.exception))
        self.assertTrue((self.dir / "LIB_5.zip").exists())

    def test_redirects_are_refused(self):
        h = cc._NoRedirect()
        with self.assertRaises(cc.CseError):
            h.redirect_request(None, None, 302, "Found", {}, "https://evil.example/")




BUILD_SCRIPT = ("CRCW08051R00FNEB\r\nCreateFootprintInLib, RESC2012X50N, \r\n"
                "CreateRectangularSMD, -0.95, 0, 0.95, 1.45, 0, 1, 0, 90\r\n"
                "AssignSTEPmodel, CRCW08051R00FNEB.stp, 90, 0, 0, 1, 0.6299999952316284, 0\r\n"
                "CreateComponentInLib, CRCW08051R00FNEB, Resistor, R?\r\n")


class PlacementTests(unittest.TestCase):
    def test_parse(self):
        p = cc.parse_placement(BUILD_SCRIPT)
        self.assertEqual(p.step_name, "CRCW08051R00FNEB.stp")
        self.assertEqual((p.rot_x, p.rot_y, p.rot_z, p.x_mm, p.z_mm), (90, 0, 0, 1, 0))
        self.assertAlmostEqual(p.y_mm, 0.63, places=5)
        self.assertFalse(p.is_identity)
        self.assertTrue(cc.parse_placement("AssignSTEPmodel, a.stp, 0, 0, 0, 0, 0, 0").is_identity)

    def test_parse_rejects_bad_or_missing(self):
        self.assertIsNone(cc.parse_placement("CreateFootprintInLib, X, "))
        self.assertIsNone(cc.parse_placement("AssignSTEPmodel, a.stp, 90, 0, x, 0, 0, 0"))
        self.assertIsNone(cc.parse_placement("AssignSTEPmodel, a.stp, 90, 0, 0, 1e9, 0, 0"))
        self.assertIsNone(cc.parse_placement("AssignSTEPmodel, a.stp, nan, 0, 0, 0, 0, 0"))
        self.assertEqual(cc.parse_placement("AssignSTEPmodel, ../../a.stp, 0, 0, 0, 0, 0, 0"
                                            ).step_name, "a.stp")

    def test_placement_request(self):
        seen = {}
        client = cc.CseClient("me@example.com", "pw")

        def fake_open(req, timeout):
            seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
            return FakeResp(BUILD_SCRIPT.encode(), {"Content-Type": "text/plain;charset=UTF-8"})
        with mock.patch.object(cc._opener, "open", side_effect=fake_open):
            p = client.placement(17641087)
        self.assertEqual(seen["url"], "https://ad.componentsearchengine.com/ga/model.php?"
                                      "partID=17641087&st=4&lt=4&pi=2")
        self.assertTrue(seen["auth"].startswith("Basic "))
        self.assertEqual(p.rot_x, 90)

    def test_placement_server_error(self):
        resp = FakeResp(b"Error: Data entry for this part ID is not yet complete.",
                        {"Content-Type": "text/plain"})
        with mock.patch.object(cc._opener, "open", side_effect=[resp]):
            with self.assertRaises(cc.CseError) as ctx:
                cc.CseClient("a", "b").placement(1)
        self.assertIn("not yet complete", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
