import os
import tempfile
import unittest
from pathlib import Path

from alm.cse import DownloadWatcher, claim, human_size, name_matches


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.clock = Clock()

    def tearDown(self):
        self._tmp.cleanup()

    def watcher(self, mpn="STM32F103C8T6", timeout=60):
        return DownloadWatcher(self.dir, mpn, timeout=timeout, stable_secs=2, clock=self.clock)

    def test_name_matching(self):
        self.assertTrue(name_matches("LIB_STM32F103C8T6.zip", "stm32f103c8t6"))
        self.assertTrue(name_matches("LIB_MCP23017T_E_ML.zip", "MCP23017T-E/ML"))
        self.assertFalse(name_matches("LIB_MCP23017.zip", "MCP23017T-E/ML"))

    def test_existing_files_are_ignored_and_waiting_reported(self):
        (self.dir / "old.zip").write_bytes(b"x" * 10)
        w = self.watcher()
        self.assertEqual(w.poll().state, "waiting")

    def test_chrome_style_download(self):
        w = self.watcher()
        part = self.dir / "LIB_STM32F103C8T6.zip.crdownload"
        part.write_bytes(b"x" * 5000)
        st = w.poll()
        self.assertEqual(st.state, "downloading")
        self.assertEqual(st.size, 5000)
        self.assertIn("LIB_STM32F103C8T6.zip", st.message)
        os.replace(part, self.dir / "LIB_STM32F103C8T6.zip")
        self.assertEqual(w.poll().state, "downloading")     # not yet stable
        self.clock.t += 3
        st = w.poll()
        self.assertEqual(st.state, "ready")
        self.assertEqual(st.path.name, "LIB_STM32F103C8T6.zip")
        self.assertTrue(st.matches_mpn)

    def test_firefox_style_empty_final_file_plus_part(self):
        w = self.watcher()
        (self.dir / "LIB_X.zip").write_bytes(b"")
        (self.dir / "LIB_X.zip.part").write_bytes(b"x" * 100)
        self.clock.t += 5
        self.assertEqual(w.poll().state, "downloading")
        (self.dir / "LIB_X.zip.part").unlink()
        (self.dir / "LIB_X.zip").write_bytes(b"x" * 100)
        w.poll()
        self.clock.t += 3
        self.assertEqual(w.poll().state, "ready")

    def test_growing_file_is_not_ready(self):
        w = self.watcher()
        f = self.dir / "a.zip"
        f.write_bytes(b"x" * 10)
        w.poll()
        self.clock.t += 3
        f.write_bytes(b"x" * 20)          # still growing
        self.assertEqual(w.poll().state, "downloading")
        self.clock.t += 3
        self.assertEqual(w.poll().state, "ready")

    def test_prefers_file_named_after_mpn(self):
        w = self.watcher()
        (self.dir / "LIB_OTHER.zip").write_bytes(b"x" * 10)
        (self.dir / "LIB_STM32F103C8T6.zip").write_bytes(b"x" * 10)
        w.poll()
        self.clock.t += 3
        self.assertEqual(w.poll().path.name, "LIB_STM32F103C8T6.zip")

    def test_ignores_other_file_types_and_times_out(self):
        w = self.watcher(timeout=60)
        (self.dir / "photo.jpg").write_bytes(b"x" * 10)
        self.clock.t += 61
        self.assertEqual(w.poll().state, "timeout")

    def test_claim_copies_into_work_dir(self):
        src = self.dir / "LIB_A.zip"
        src.write_bytes(b"abc")
        copy = claim(src, self.dir / "work")
        self.assertTrue(copy.exists())
        self.assertEqual(copy.read_bytes(), b"abc")
        self.assertTrue(src.exists())                    # original left in Downloads
        self.assertEqual(human_size(2 * 1048576), "2.0 MB")


if __name__ == "__main__":
    unittest.main()
