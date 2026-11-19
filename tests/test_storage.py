import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alm.config import Settings
from alm.credentials import CredentialStore
from alm.db import Database
from alm.validation import OLE_MAGIC, check_library


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class SettingsTests(StorageTestCase):
    def test_persist_and_reload(self):
        s = Settings(self.dir / "settings.json")
        s.set("schlib", r"D:\Libs\ICs.SchLib")
        self.assertEqual(Settings(self.dir / "settings.json").get("schlib"), r"D:\Libs\ICs.SchLib")
        self.assertFalse((self.dir / "settings.json.tmp").exists())

    def test_corrupt_file_falls_back_to_defaults(self):
        (self.dir / "settings.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(Settings(self.dir / "settings.json").get("pcblib"), "")


class DatabaseTests(StorageTestCase):
    def test_component_roundtrip_by_normalized_mpn(self):
        db = Database(self.dir / "t.db")
        db.add_component("MCP23017T-E/ML", files=["a.SchLib"], manufacturer="Microchip",
                         dest_schlib="x.SchLib", footprint_name="QFN28")
        self.assertEqual(len(db.find_component("mcp23017t-e/ml")), 1)
        self.assertEqual(db.find_component("MCP23017-E/ML"), [])   # different part
        self.assertEqual(db.find_component("MCP23017T-E/ML")[0]["files"], ["a.SchLib"])
        db.log_search("X", {"Mouser API": "Searched"}, 0)
        db.close()

    def test_rejects_unknown_fields(self):
        db = Database(self.dir / "t.db")
        with self.assertRaises(ValueError):
            db.add_component("X", bogus="1")
        db.close()


@unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
class CredentialTests(StorageTestCase):
    def test_encrypted_roundtrip_and_delete(self):
        store = CredentialStore(self.dir / "cred.json")
        store.set("test_key", "s3cret-value")
        raw = (self.dir / "cred.json").read_text(encoding="utf-8")
        self.assertNotIn("s3cret-value", raw)          # not stored in clear text
        self.assertEqual(store.get("test_key"), "s3cret-value")
        store.set("test_key", "")
        self.assertEqual(store.get("test_key"), "")
        self.assertEqual(json.loads((self.dir / "cred.json").read_text(encoding="utf-8")), {})

    def test_env_override(self):
        store = CredentialStore(self.dir / "cred.json")
        with mock.patch.dict(os.environ, {"ALM_TEST_KEY": "from-env"}):
            self.assertEqual(store.get("test_key"), "from-env")


class ValidationTests(StorageTestCase):
    def test_check_library(self):
        good = self.dir / "a.SchLib"
        good.write_bytes(OLE_MAGIC + b"\0" * 100)
        bad = self.dir / "b.SchLib"
        bad.write_text("|HEADER=Protel for Windows", encoding="ascii")
        self.assertIsNone(check_library(str(good), ".SchLib"))
        self.assertIn("not a binary", check_library(str(bad), ".SchLib"))
        self.assertIn("Expected", check_library(str(good), ".PcbLib"))
        self.assertIn("not found", check_library(str(self.dir / "c.SchLib"), ".SchLib"))


if __name__ == "__main__":
    unittest.main()
