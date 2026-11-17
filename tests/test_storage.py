import os
import tempfile
import time
import unicodedata
import unittest
from pathlib import Path

from notion_sync import storage
from notion_sync.config import ConfigError, norm_id


class StorageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name) / "proj"
        self.project.mkdir()
        self.ws = storage.Workspace(self.project, "Docs/Notion", ".notion-sync")

    def tearDown(self):
        self._tmp.cleanup()

    def test_windows_safe_filenames(self):
        sid = "00000000-0000-0000-0000-0000e5f6a7b8"
        name = storage.safe_filename('전투: 시스템 <v2>/a|b?*"', sid)
        self.assertFalse(set(name) & set('<>:"/\\|?*'))
        self.assertTrue(name.endswith("__e5f6a7b8.md"))
        self.assertEqual(storage.safe_filename("CON", sid), "_CON__e5f6a7b8.md")
        self.assertEqual(storage.safe_filename("nul.txt", sid), "_nul.txt__e5f6a7b8.md")
        self.assertEqual(storage.safe_filename("끝에 점...  ", sid), "끝에 점__e5f6a7b8.md")
        self.assertEqual(storage.safe_filename("", sid), "Untitled__e5f6a7b8.md")
        self.assertLessEqual(len(storage.safe_filename("가" * 300, sid)), 80 + len("__e5f6a7b8.md"))

    def test_nfd_title_becomes_nfc(self):
        nfd = unicodedata.normalize("NFD", "세계관")
        self.assertNotEqual(nfd, "세계관")
        self.assertEqual(storage.safe_filename(nfd, "0" * 24 + "a1b2c3d4"), "세계관__a1b2c3d4.md")

    def test_atomic_write_and_no_tmp_left(self):
        path = self.ws.doc("a.md")
        storage.write_text(self.ws, path, "첫 줄\n")
        storage.write_text(self.ws, path, "둘째\n")
        self.assertEqual(path.read_bytes(), "둘째\n".encode("utf-8"))  # LF 고정
        self.assertEqual([p.name for p in path.parent.iterdir()], ["a.md"])

    def test_write_outside_roots_blocked(self):
        for bad in (self.project / "Assets" / "x.cs", self.project / "CLAUDE.md", self.project / "x.md",
                    self.ws.docs / ".." / ".." / "Assets" / "a.md", self.ws.docs / "evil.prefab"):
            with self.assertRaises(storage.PathError, msg=str(bad)):
                storage.write_text(self.ws, bad, "x")
        self.assertFalse((self.project / "Assets").exists())

    def test_symlink_escape_blocked(self):
        outside = Path(self._tmp.name) / "outside"
        outside.mkdir()
        (self.project / "Docs").mkdir(exist_ok=True)
        link = self.project / "Docs" / "Linked"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("심볼릭 링크 생성 권한 없음(Windows 일반 사용자)")
        with self.assertRaises(storage.PathError):
            storage.Workspace(self.project, "Docs/Linked", ".notion-sync")
        self.ws.docs.mkdir(parents=True, exist_ok=True)
        os.symlink(outside / "x.md", self.ws.docs / "leak.md")
        with self.assertRaises(storage.PathError):
            storage.write_text(self.ws, self.ws.docs / "leak.md", "x")
        self.assertFalse((outside / "x.md").exists())

    def test_case_only_rename(self):
        a = self.ws.doc("abc.md")
        storage.write_text(self.ws, a, "x")
        storage.move(self.ws, a, self.ws.doc("ABC.md"))
        self.assertEqual([p.name for p in self.ws.docs.iterdir()], ["ABC.md"])

    def test_lock(self):
        with storage.lock(self.ws):
            with self.assertRaises(storage.LockError):
                with storage.lock(self.ws):
                    pass
        with storage.lock(self.ws):  # 해제 후 다시 획득 가능
            pass
        lock = self.ws.st("sync.lock")
        lock.write_text("old")
        old = time.time() - storage.LOCK_STALE_SECONDS - 10
        os.utime(lock, (old, old))
        with storage.lock(self.ws):  # 오래된 잠금은 회수
            pass

    def test_norm_id(self):
        want = "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
        for url in ("https://www.notion.so/team/Dead-Beef-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d?pvs=4",
                    "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d", want.upper(),
                    "https://notion.so/1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d#abc"):
            self.assertEqual(norm_id(url), want)
        with self.assertRaises(ConfigError):
            norm_id("https://notion.so/no-id-here")


if __name__ == "__main__":
    unittest.main()
