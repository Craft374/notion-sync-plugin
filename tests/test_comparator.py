import unicodedata
import unittest

from notion_sync.comparator import CONFLICT, MODIFIED, RENAMED, UNCHANGED, ADDED, classify, normalize, sha256


class ComparatorTest(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("a  \r\nb\t\r\n\r\n\r\n"), "a\nb\n")
        self.assertEqual(normalize("\n\nx"), "x\n")
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize(unicodedata.normalize("NFD", "한글")), "한글\n")
        self.assertEqual(sha256(normalize("a\r\n")), sha256(normalize("a\n")))

    def test_classify(self):
        b, x, y = "base", "x", "y"
        cases = [
            ((None, None, x, False), ADDED),          # 신규
            ((b, None, x, False), ADDED),             # 로컬 파일 사라짐 → 복원
            ((b, b, None, False), UNCHANGED),         # Fast: 수정 시각 동일
            ((b, b, b, False), UNCHANGED),
            ((b, b, None, True), RENAMED),
            ((b, b, x, False), MODIFIED),
            ((b, b, x, True), MODIFIED),
            ((b, x, b, False), CONFLICT),             # 로컬만 수정
            ((b, x, y, False), CONFLICT),             # 양쪽 수정
            ((b, x, x, False), UNCHANGED),            # 로컬을 원격과 같게 맞춤 → 해결
            ((None, x, y, False), CONFLICT),          # 관리되지 않던 파일
            ((None, x, x, False), UNCHANGED),         # 같은 내용이면 채택
        ]
        for args, want in cases:
            self.assertEqual(classify(*args), want, args)


if __name__ == "__main__":
    unittest.main()
