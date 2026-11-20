import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from alm import snapeda_client as sc

SEARCH = {"query_type": "mpn", "results": [
    {"unipart_id": "964729", "part_number": "CRCW08051R00FNEB", "manufacturer": "Vishay Dale",
     "has_symbol": 1, "has_footprint": 1, "has_3d": 1, "package_type": "2012",
     "short_description": "1 Ohm 0805"},
    {"unipart_id": "1", "part_number": "CRCW08051R00FNEA", "manufacturer": "Vishay",
     "has_symbol": 1, "has_footprint": 1},
    {"unipart_id": "2", "part_number": "crcw08051r00fneb ", "manufacturer": "NoFootprint",
     "has_symbol": 1, "has_footprint": 0},
    {"unipart_id": "x", "part_number": "bad id"}]}

LOGIN_HTML = ("<form id='s-form-login' method='POST' action='/account/login/?next=/home/'>"
              "<input type='hidden' name='csrfmiddlewaretoken' value='Tok123abc' />")


class Headers(dict):
    def get(self, k, d=None):
        return super().get(k, d)


class ParseTests(unittest.TestCase):
    def test_search_and_exact(self):
        parts = sc.parse_search(SEARCH)
        self.assertEqual(len(parts), 3)                       # invalid id skipped
        exact = sc.exact_matches(parts, "CRCW08051R00FNEB")
        self.assertEqual([p.unipart_id for p in exact], [964729])   # suffix variant + no fp out
        self.assertTrue(exact[0].has_3d)
        self.assertEqual(exact[0].description, "1 Ohm 0805")

    def test_bad_search_shape(self):
        with self.assertRaises(sc.SnapError):
            sc.parse_search(["x"])

    def test_download_path_validation(self):
        good = "/parts/snapapi/download-component/3029412/964729/%s"
        self.assertEqual(sc.download_path({"download_url": good}), good)
        for bad in ("https://evil.example/x/%s", "/parts/snapapi/download-component/a/1/%s",
                    "/parts/snapapi/download-component/1/1/%s/../../x", ""):
            with self.assertRaises(sc.SnapError):
                sc.download_path({"download_url": bad})

    def test_form_token_and_filename(self):
        self.assertEqual(sc.form_token(LOGIN_HTML), "Tok123abc")
        self.assertEqual(sc.form_token("<html></html>"), "")
        self.assertEqual(sc.safe_filename("https://s3.x/a/b/CRCW.zip?sig=1", 5), "CRCW.zip")
        self.assertEqual(sc.safe_filename("https://s3.x/a/..%2F..%2Fevil.exe", 5),
                         "SnapEDA_5.zip")


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.c = sc.SnapClient("me@example.com", "s3cret pw")
        self.cookies = {"csrftoken": "csrf1", "sessionid": "anon"}
        self.c._cookie = lambda name: self.cookies.get(name, "")
        sleep = mock.patch.object(sc.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_refuses_other_hosts(self):
        with self.assertRaises(sc.SnapError):
            self.c._request("https://evil.example/api")

    def test_login_success_and_form_fields(self):
        sent = []

        def req(url, data=None, headers=None, allow_redirect=False):
            sent.append((url, data))
            if data is None:
                return 200, Headers(), LOGIN_HTML.encode()
            self.cookies["sessionid"] = "user-session"
            return 302, Headers(Location="/home/"), b""
        self.c._request = req
        self.c.login()
        self.assertTrue(self.c._logged_in)
        url, form = sent[1]
        self.assertTrue(url.startswith("https://www.snapeda.com/account/login/"))
        self.assertEqual((form["username"], form["password"], form["csrfmiddlewaretoken"]),
                         ("me@example.com", "s3cret pw", "Tok123abc"))

    def test_login_rejected_without_password_in_message(self):
        self.c._request = lambda url, data=None, headers=None, allow_redirect=False: (
            200, Headers(), LOGIN_HTML.encode())
        with self.assertRaises(sc.SnapAuthError) as ctx:
            self.c.login()
        self.assertNotIn("s3cret", str(ctx.exception))

    def test_login_needs_credentials(self):
        with self.assertRaises(sc.SnapAuthError):
            sc.SnapClient("", "").login()

    def test_file_url_signs_in_again_when_session_expired(self):
        calls = {"login": 0, "dl": 0}

        def login():
            calls["login"] += 1
            self.c._logged_in = True
        self.c.login = login

        def req(url, data=None, headers=None, allow_redirect=False):
            if "get_part_for_unipart" in url:
                return 200, Headers(), json.dumps({"download_url":
                    "/parts/snapapi/download-component/3029412/964729/%s"}).encode()
            self.assertTrue(url.endswith("/3029412/964729/altium_native"))
            calls["dl"] += 1
            if calls["dl"] == 1:
                return 302, Headers(Location="/account/login/?next=/parts/x"), b""
            return 200, Headers(), b'{"url": "https://s3.example/CRCW.zip"}'
        self.c._request = req
        self.assertEqual(self.c.file_url(964729), "https://s3.example/CRCW.zip")
        self.assertEqual(calls, {"login": 2, "dl": 2})

    def test_saved_session_is_reused_without_sign_in(self):
        c = sc.SnapClient("u", "p", session="abc123def456")
        self.assertEqual(c.session, "abc123def456")
        c.login = mock.Mock(side_effect=AssertionError("must not sign in"))
        c._request = lambda url, data=None, headers=None, allow_redirect=False: (
            200, Headers(), json.dumps({"download_url":
                "/parts/snapapi/download-component/1/2/%s"}).encode()
            if "get_part" in url else b'{"url": "https://s3.example/x.IntLib"}')
        self.assertEqual(c.file_url(2), "https://s3.example/x.IntLib")
        self.assertEqual(sc.SnapClient("u", "p", session="bad;cookie").session, "")

    def test_intlib_download_and_names(self):
        self.assertEqual(sc.library_filename("https://s3.x/a/CRCW.IntLib?sig=1", 5), "CRCW.IntLib")
        self.assertEqual(sc.library_filename("https://s3.x/a/evil.exe", 5), "")

        class Resp(io.BytesIO):
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        body = sc.OLE_MAGIC + b"\0" * 100
        self.c.file_url = lambda uid, fmt=sc.ALTIUM_FORMAT: "https://s3.example/CRCW.IntLib"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(sc, "_plain_opener") as po:
            po.return_value.open.return_value = Resp(body)
            path = self.c.download(sc.SnapPart(1, "X"), Path(d))
            self.assertEqual((path.name, path.read_bytes()), ("CRCW.IntLib", body))

    def test_file_url_error_message(self):
        self.c._logged_in = True
        self.c._request = lambda url, data=None, headers=None, allow_redirect=False: (
            200, Headers(), json.dumps({"download_url":
                "/parts/snapapi/download-component/1/2/%s"}).encode()
            if "get_part" in url else b'{"error": "Daily download limit reached"}')
        with self.assertRaises(sc.SnapError) as ctx:
            self.c.file_url(2)
        self.assertIn("Daily download limit", str(ctx.exception))

    def test_download_saves_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("CRCW/CRCW.SchLib", b"s")
            z.writestr("CRCW/CRCW.PcbLib", b"p")
        body = buf.getvalue()

        class Resp(io.BytesIO):
            headers = Headers({"Content-Length": str(len(body))})

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        self.c.file_url = lambda uid, fmt=sc.ALTIUM_FORMAT: "https://s3.example/CRCW.zip"
        part = sc.SnapPart(964729, "CRCW08051R00FNEB")
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sc, "_plain_opener") as po:
            po.return_value.open.return_value = Resp(body)
            path = self.c.download(part, Path(d))
            self.assertEqual(path.name, "CRCW.zip")
            self.assertEqual(path.read_bytes(), body)
            req = po.return_value.open.call_args[0][0]
            self.assertIsNone(req.get_header("Cookie"))       # session never sent to S3


if __name__ == "__main__":
    unittest.main()
