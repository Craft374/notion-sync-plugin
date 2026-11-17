import unittest

from notion_sync.client import NotFoundError
from notion_sync.scanner import scan
from tests.fake import FakeNotion, uid


class ScannerTest(unittest.TestCase):
    def test_finds_child_pages_and_database_rows(self):
        fake = FakeNotion()
        res = scan(fake, [uid(1)])
        self.assertTrue(res.complete)
        self.assertEqual({p.title for p in res.pages.values()}, {"게임 기획서", "세계관", "전투시스템", "스킬", "검", "방패"})
        self.assertEqual(res.pages[uid(4)].parent_id, uid(3))
        self.assertIsNone(res.pages[uid(1)].parent_id)
        self.assertTrue(all(p.markdown for p in res.pages.values()))
        self.assertEqual(sorted(fake.load_calls), sorted(res.pages))  # 페이지당 한 번만 조회

    def test_failure_marks_incomplete(self):
        fake = FakeNotion()
        fake.errors[uid(3)] = NotFoundError(400, "Invalid input.")
        res = scan(fake, [uid(1)])
        self.assertFalse(res.complete)
        self.assertNotIn(uid(4), res.pages)
        self.assertTrue(any("공개 공유" in w for w in res.warnings))

    def test_duplicate_roots_visited_once(self):
        fake = FakeNotion()
        scan(fake, [uid(1), uid(1), uid(3)])
        self.assertEqual(fake.load_calls.count(uid(3)), 1)


if __name__ == "__main__":
    unittest.main()
