import tempfile
import unittest
import zipfile
from pathlib import Path

from alm.altium import bridge
from alm.importer import ImportRequest, finish_values, job_values, validate
from alm.sources import UnsafeArchive, resolve_source
from alm.validation import OLE_MAGIC

RESULT_OK = """STATUS=OK
VERIFY_FOOTPRINT=FP1|primitives 205/205
ADDED_FOOTPRINT=FP1
LINK=PART1|FP1|Dest.PcbLib
PARAM=LCSC Part # added ""
ADDED_SYMBOL=PART1
VERIFY_SYMBOL=PART1|pins 32/32|objects 46/43
PINCHECK=PART1|FP1|32|32||
END=1
"""


def make_lib(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(OLE_MAGIC + b"\0" * 64)
    return path


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ResultParsingTests(unittest.TestCase):
    def test_success(self):
        r = bridge.parse_result(RESULT_OK)
        self.assertTrue(r.ok)
        self.assertEqual(r.added_symbols, ["PART1"])
        self.assertEqual(r.links, [("PART1", "FP1", "Dest.PcbLib")])
        self.assertTrue(r.pins_match)
        self.assertIn("pin functions still need verification", r.summary())

    def test_mismatch_and_zero_pins_are_not_matches(self):
        r = bridge.parse_result("STATUS=OK\nPINCHECK=P|F|4|5||EP\nEND=1")
        self.assertFalse(r.pins_match)
        self.assertIn("PIN/PAD MISMATCH", r.summary())
        r = bridge.parse_result("STATUS=OK\nPINCHECK=P|F|0|5||1 2\nEND=1")
        self.assertFalse(r.pins_match)
        self.assertIn("PIN CHECK NOT POSSIBLE", r.summary())

    def test_error(self):
        r = bridge.parse_result("STATUS=ERROR\nERROR=boom\nEND=1")
        self.assertFalse(r.ok)
        self.assertIn("ERROR: boom", r.summary())

    def test_library_search_check_and_flags(self):
        self.assertIsNone(bridge.parse_result("STATUS=OK\nEND=1").cache_ok)
        r = bridge.parse_result("STATUS=OK\nVERIFY_CACHE=FP|A|ok\nVERIFY_CACHE=FP2|not found\n"
                                "SCH_WAS_OPEN=1\nEND=1")
        self.assertEqual(r.cache, [("FP|A", True), ("FP2", False)])
        self.assertFalse(r.cache_ok)
        self.assertEqual(r.flags, {"SCH_WAS_OPEN": "1"})
        self.assertIn("does not find footprint FP2", r.summary())


class JobTests(TmpCase):
    def test_write_job_substitutes_paths_and_escapes(self):
        job_dir = self.dir / "it's"
        job, script, result = bridge.write_job(job_dir, {"Action": "import", "X": "a\nb"})
        text = script.read_text(encoding="utf-8-sig")
        self.assertNotIn("{{JOB_FILE}}", text)
        self.assertNotIn("{{RESULT_FILE}}", text)
        self.assertIn("it''s", text)                       # Pascal string escaping
        raw = job.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))   # BOM so Altium reads UTF-8
        self.assertIn("X=a b", raw.decode("utf-8-sig"))    # no line breaks inside values

    def test_job_values_always_include_lcsc_param(self):
        req = ImportRequest("a", "b", "c", "d", params={"Manufacturer": "M"})
        v = job_values(req)
        names = [v[f"Param{i}Name"] for i in range(1, int(v["ParamCount"]) + 1)]
        self.assertIn("LCSC Part #", names)
        self.assertEqual(v[f"Param{names.index('LCSC Part #') + 1}Value"], "")

    def test_job_values_component_and_keep_open(self):
        v = job_values(ImportRequest("a", "b", "c", "d", component="U1", step_file="m.stp"))
        self.assertEqual((v["Component"], v["KeepOpen"], v["StepFile"]), ("U1", "1", "m.stp"))

    def test_added_3d_is_reported(self):
        r = bridge.parse_result("STATUS=OK\nADDED_3D=FP1|part.stp\nEND=1")
        self.assertEqual(r.added_3d, [("FP1", "part.stp")])
        self.assertIn("Added 3D model: part.stp -> FP1", r.summary())

    def test_finish_values(self):
        req = ImportRequest("a", "b", "c.SchLib", "d.PcbLib")
        prep = bridge.parse_result("STATUS=OK\nPCB_WAS_OPEN=1\nEND=1")
        rep = bridge.parse_result("STATUS=OK\nLINK=S|FP1|d.PcbLib\nLINK=S2|FP1|d.PcbLib\nEND=1")
        v = finish_values(req, prep, rep)
        self.assertEqual((v["FootprintCount"], v["Footprint1"]), ("1", "FP1"))
        self.assertEqual((v["ReopenSch"], v["ReopenPcb"], v["CloseDocs"]), ("0", "1", "1"))
        failed = bridge.parse_result("STATUS=ERROR\nERROR=x\nEND=1")
        self.assertEqual(finish_values(req, prep, failed)["FootprintCount"], "0")

    def test_validate_rejects_same_file(self):
        sch = make_lib(self.dir / "a.SchLib")
        pcb = make_lib(self.dir / "a.PcbLib")
        errs = validate(ImportRequest(str(sch), str(pcb), str(sch), str(pcb)))
        self.assertTrue(any("same file" in e for e in errs))


class SourceResolutionTests(TmpCase):
    def test_libpkg_with_documents(self):
        sch = make_lib(self.dir / "sub" / "P.SchLib")
        pcb = make_lib(self.dir / "sub" / "P.PcbLib")
        pkg_file = self.dir / "P.LibPkg"
        pkg_file.write_text("[Document1]\nDocumentPath=sub\\P.SchLib\n[Document2]\n"
                            "DocumentPath=sub\\P.PcbLib\nOutputDocumentPath1=\n", encoding="utf-8")
        pkg = resolve_source(pkg_file, self.dir / "x")
        self.assertEqual((pkg.schlib, pkg.pcblib), (sch, pcb))

    def test_libpkg_without_documents_scans_folder(self):
        sch = make_lib(self.dir / "P.SchLib")
        pcb = make_lib(self.dir / "P.PcbLib")
        (self.dir / "P.step").write_text("ISO-10303-21;")
        (self.dir / "P.LibPkg").write_text("[Design]\nOutputDocumentPath1=\n", encoding="utf-8")
        pkg = resolve_source(self.dir / "P.LibPkg", self.dir / "x")
        self.assertEqual((pkg.schlib, pkg.pcblib), (sch, pcb))
        self.assertEqual(len(pkg.step_files), 1)

    def test_schlib_finds_sibling_pcblib(self):
        sch = make_lib(self.dir / "A.SchLib")
        pcb = make_lib(self.dir / "B.PcbLib")
        pkg = resolve_source(sch, self.dir / "x")
        self.assertEqual((pkg.schlib, pkg.pcblib), (sch, pcb))

    def test_zip_extracts_safely(self):
        z = self.dir / "dl.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("Altium/P.SchLib", OLE_MAGIC)
            zf.writestr("Altium/P.PcbLib", OLE_MAGIC)
        pkg = resolve_source(z, self.dir / "extract")
        self.assertEqual(pkg.schlib.name, "P.SchLib")
        self.assertTrue(str(pkg.schlib).startswith(str((self.dir / "extract").resolve())))

    def test_zip_traversal_rejected(self):
        z = self.dir / "evil.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../../evil.txt", "x")
        with self.assertRaises(UnsafeArchive):
            resolve_source(z, self.dir / "extract")
        self.assertFalse((self.dir.parent / "evil.txt").exists())

    def test_intlib(self):
        lib = make_lib(self.dir / "X.IntLib")
        pkg = resolve_source(lib, self.dir / "x")
        self.assertEqual(pkg.intlib, lib)


if __name__ == "__main__":
    unittest.main()
