import base64
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


if __name__ == "__main__":
    unittest.main()
