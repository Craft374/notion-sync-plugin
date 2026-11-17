"""명세 13장 시나리오를 가짜 Notion 으로 검증한다. 실제 토큰을 쓰지 않는다."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from notion_sync import storage, sync_engine
from notion_sync.client import NotFoundError, NotionError
from tests.fake import FakeNotion, uid

ROOT, WORLD, COMBAT, SKILL, SWORD = uid(1), uid(2), uid(3), uid(4), uid(6)


def items(rec, status):
    return sorted(i["file"] for i in rec["items"] if i["status"] == status)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project = Path(self._tmp.name)
        state = self.project / ".notion-sync"
        state.mkdir()
        (state / "config.json").write_text(
            json.dumps({"docs_dir": "Docs/Notion", "root_pages": [ROOT]}), encoding="utf-8")
        self.docs = self.project / "Docs" / "Notion"
        self.fake = FakeNotion()

    def tearDown(self):
        self._tmp.cleanup()

    def sync(self, **kw):
        return sync_engine.sync(self.project, self.fake, **kw)

    def manifest(self):
        return json.loads((self.project / ".notion-sync" / "manifest.json").read_text(encoding="utf-8"))["pages"]

    def file_of(self, pid):
        return self.docs / self.manifest()[pid]["file"]

    def mtimes(self):
        return {p.relative_to(self.docs).as_posix(): p.stat().st_mtime_ns for p in self.docs.rglob("*.md")}

    # 1
    def test_initial_sync(self):
        rec = self.sync()
        self.assertEqual(rec["counts"]["ADDED"], 6)  # 루트, 하위 2, 손자 1, DB 행 2
        self.assertTrue(rec["success"])
        combat = (self.docs / "게임 기획서/전투시스템.md").read_text(encoding="utf-8")
        self.assertIn("| 기본 공격력 | 10 |", combat)
        self.assertIn("```c#\nint atk = 10;\n```", combat)
        self.assertIn("- ▶ 스킬 목록\n  - 파이어볼", combat)
        self.assertIn("- 📄 [스킬](<전투시스템/스킬.md>)", combat)  # 자기 폴더 기준 상대 경로
        root = (self.docs / "게임 기획서.md").read_text(encoding="utf-8")
        self.assertIn("**🗂 아이템 DB**", root)
        self.assertIn("- [검](<게임 기획서/검.md>)", root)
        self.assertIn("- 📄 [전투시스템](<게임 기획서/전투시스템.md>)", root)
        self.assertIn("- **상태**: 완료", (self.docs / "게임 기획서/검.md").read_text(encoding="utf-8"))
        state = self.project / ".notion-sync"
        self.assertTrue((state / "CHANGELOG.md").exists())
        self.assertTrue((state / "history" / f"{rec['run_id']}.json").exists())
        self.assertEqual(json.loads((state / "run.json").read_text(encoding="utf-8"))["status"], "done")
        self.assertFalse((state / "sync.lock").exists())

    # 2
    def test_modified_page_only_rewrites_that_file(self):
        self.sync()
        before = self.mtimes()
        old = self.file_of(COMBAT).read_text(encoding="utf-8")
        self.fake.edit(COMBAT, body=[["table", [["항목", "값"], ["기본 공격력", "15"]]],
                                     ["text", "치명타 확률: 5%"], ["code", "Lua", "crit = 5"]])
        rec = self.sync()
        self.assertEqual(items(rec, "MODIFIED"), ["게임 기획서/전투시스템.md"])
        after = self.mtimes()
        self.assertEqual([n for n in before if before[n] != after[n]], ["게임 기획서/전투시스템.md"])
        snap = self.project / ".notion-sync" / "snapshots" / rec["run_id"] / "게임 기획서/전투시스템.md"
        self.assertEqual(snap.read_text(encoding="utf-8"), old)
        log = (self.project / ".notion-sync" / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("MODIFIED: 게임 기획서/전투시스템.md", log)
        self.assertIn("-| 기본 공격력 | 10 |", log)
        self.assertIn("+| 기본 공격력 | 15 |", log)
        self.assertIn("+치명타 확률: 5%", log)
        self.assertIn("````diff", log)  # 본문의 ``` 보다 긴 펜스
        diff = (self.project / ".notion-sync" / "history" / f"{rec['run_id']}.diff").read_text(encoding="utf-8")
        self.assertIn("@@", diff)

    # 3
    def test_resync_without_changes_writes_nothing(self):
        self.sync()
        before = self.mtimes()
        log = (self.project / ".notion-sync" / "CHANGELOG.md").read_text(encoding="utf-8")
        rec = self.sync()
        self.assertEqual(rec["counts"]["UNCHANGED"], 6)
        self.assertEqual(before, self.mtimes())
        self.assertEqual(log, (self.project / ".notion-sync" / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_crlf_and_trailing_space_are_not_changes(self):
        self.sync()
        path = self.file_of(WORLD)
        path.write_bytes(path.read_bytes().replace(b"\n", b"  \r\n"))  # Windows 에디터/autocrlf
        self.assertEqual(self.sync()["counts"]["CONFLICT"], 0)

    def test_bom_is_not_a_change(self):
        self.sync()
        path = self.file_of(WORLD)
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        self.assertEqual(self.sync()["counts"]["CONFLICT"], 0)

    # 4
    def test_new_page_added(self):
        self.sync()
        self.fake.add(uid(8), "퀘스트", WORLD, [["text", "메인 퀘스트"]])
        rec = self.sync()
        self.assertEqual(items(rec, "ADDED"), ["게임 기획서/세계관/퀘스트.md"])
        self.assertEqual(items(rec, "MODIFIED"), ["게임 기획서/세계관.md"])  # 하위 페이지 링크 추가

    # 5
    def test_title_change_renames_file(self):
        self.sync()
        self.fake.edit(SKILL, title="스킬 설정")
        rec = self.sync()
        self.assertEqual(items(rec, "MODIFIED"), ["게임 기획서/전투시스템.md", "게임 기획서/전투시스템/스킬 설정.md"])
        self.assertFalse((self.docs / "게임 기획서/전투시스템/스킬.md").exists())
        self.assertIn("데미지 30", (self.docs / "게임 기획서/전투시스템/스킬 설정.md").read_text(encoding="utf-8"))
        self.assertIn("(<전투시스템/스킬 설정.md>)", self.file_of(COMBAT).read_text(encoding="utf-8"))

    def test_parent_rename_moves_children_and_prunes_folder(self):
        self.sync()
        self.fake.edit(COMBAT, title="전투")
        rec = self.sync()
        self.assertEqual(self.manifest()[SKILL]["file"], "게임 기획서/전투/스킬.md")
        self.assertIn("데미지 30", (self.docs / "게임 기획서/전투/스킬.md").read_text(encoding="utf-8"))
        self.assertFalse((self.docs / "게임 기획서/전투시스템").exists())  # 비게 된 옛 폴더는 정리
        self.assertEqual(rec["counts"]["CONFLICT"] + rec["counts"]["ERROR"], 0)

    def test_duplicate_titles_get_id_only_when_needed(self):
        self.fake.edit(uid(7), title="검")  # 같은 DB에 '검' 두 개
        self.sync()
        files = sorted(e["file"] for e in self.manifest().values())
        self.assertIn("게임 기획서/검__00000006.md", files)
        self.assertIn("게임 기획서/검__00000007.md", files)
        self.assertIn("게임 기획서/세계관.md", files)

    def test_title_swap_converges_without_conflict(self):
        self.sync()
        self.fake.edit(WORLD, title="전투시스템")
        self.fake.edit(COMBAT, title="세계관")
        for _ in range(2):  # 다른 페이지가 쓰던 이름은 한 번 ID를 붙여 피했다가 다음 sync 에 제자리로
            rec = self.sync()
            self.assertEqual(rec["counts"]["CONFLICT"] + rec["counts"]["ERROR"], 0)
        self.assertEqual(self.manifest()[WORLD]["file"], "게임 기획서/전투시스템.md")
        self.assertEqual(self.manifest()[COMBAT]["file"], "게임 기획서/세계관.md")
        self.assertEqual(self.manifest()[SKILL]["file"], "게임 기획서/세계관/스킬.md")
        self.assertIn("데미지 30", self.file_of(SKILL).read_text(encoding="utf-8"))

    def test_new_page_reusing_deleted_title_gets_id(self):
        self.sync()
        self.fake.pages[WORLD]["alive"] = False
        self.sync()  # 삭제 후보, 로컬 파일은 남김
        self.fake.add(uid(9), "세계관", ROOT, [["text", "새 세계관"]])
        rec = self.sync()
        self.assertEqual(rec["counts"]["CONFLICT"], 0)
        self.assertEqual(self.manifest()[uid(9)]["file"], "게임 기획서/세계관__00000009.md")
        self.assertNotIn("새 세계관", self.file_of(WORLD).read_text(encoding="utf-8"))

    def test_missing_file_never_overwrites_other_file_on_rename(self):
        self.sync()
        self.file_of(WORLD).unlink()
        (self.docs / "게임 기획서/메모.md").write_text("내 파일\n", encoding="utf-8")
        self.fake.edit(WORLD, title="메모")
        rec = self.sync()
        self.assertEqual(items(rec, "CONFLICT"), ["게임 기획서/세계관.md"])
        self.assertEqual((self.docs / "게임 기획서/메모.md").read_text(encoding="utf-8"), "내 파일\n")

    def test_id_suffix_option(self):
        cfg = {"docs_dir": "Docs/Notion", "root_pages": [ROOT], "id_suffix": True}
        (self.project / ".notion-sync" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.sync()
        self.assertEqual(self.manifest()[SKILL]["file"],
                         "게임 기획서__00000001/전투시스템__00000003/스킬__00000004.md")

    def test_flat_layout_migrates_to_folders(self):
        self.sync()
        path = self.project / ".notion-sync" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for pid, entry in manifest["pages"].items():  # 1.1 이전처럼 모든 문서를 한 폴더에 평평하게
            flat = storage.safe_filename(entry["title"], pid)
            (self.docs / entry["file"]).rename(self.docs / flat)
            entry["file"] = flat
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        rec = self.sync()
        self.assertEqual(rec["counts"]["CONFLICT"] + rec["counts"]["ERROR"], 0)
        self.assertEqual(sorted(p.relative_to(self.docs).as_posix() for p in self.docs.rglob("*.md")),
                         sorted(e["file"] for e in self.manifest().values()))
        self.assertTrue((self.docs / "게임 기획서/전투시스템/스킬.md").exists())

    def test_rename_with_same_body_is_renamed(self):
        self.sync()
        self.fake.edit(SWORD, title="장검")  # DB 행: 본문에 제목이 # 으로 들어가므로 MODIFIED + 이동
        rec = self.sync()
        moved = [i for i in rec["items"] if i["page_id"] == SWORD][0]
        self.assertIn("게임 기획서/검.md → 게임 기획서/장검.md", moved["note"])
        self.assertFalse((self.docs / "게임 기획서/검.md").exists())
        self.assertTrue((self.docs / "게임 기획서/장검.md").exists())

    # 6
    def test_moved_subpage_keeps_file(self):
        self.sync()
        skill_mtime = self.file_of(SKILL).stat().st_mtime_ns
        self.fake.edit(SKILL, parent=WORLD)
        rec = self.sync()
        moved = [i for i in rec["items"] if i["page_id"] == SKILL][0]
        self.assertEqual(moved["status"], "RENAMED")  # Notion 계층을 따라 폴더 이동, 내용은 그대로
        self.assertIn("상위 페이지 변경", moved["note"])
        self.assertEqual(self.manifest()[SKILL]["file"], "게임 기획서/세계관/스킬.md")
        self.assertEqual(skill_mtime, self.file_of(SKILL).stat().st_mtime_ns)

    # 7
    def test_trashed_page_marked_deleted_not_removed(self):
        self.sync()
        self.fake.pages[COMBAT]["alive"] = False
        rec = self.sync()
        deleted = {i["file"]: i["note"] for i in rec["items"] if i["status"] == "DELETED"}
        self.assertIn("휴지통", deleted["게임 기획서/전투시스템.md"])
        self.assertIn("범위 밖", deleted["게임 기획서/전투시스템/스킬.md"])  # 부모가 휴지통 → 탐색 불가
        self.assertTrue((self.docs / "게임 기획서/전투시스템.md").exists())
        self.assertEqual(self.sync()["counts"]["DELETED"], 0)  # 한 번만 보고

    def test_permission_lost(self):
        self.sync()
        self.fake.pages.pop(SWORD)
        rec = self.sync()
        note = [i for i in rec["items"] if i["status"] == "DELETED"][0]["note"]
        self.assertIn("접근 불가", note)
        self.assertTrue((self.docs / "게임 기획서/검.md").exists())

    def test_incomplete_scan_never_deletes(self):
        self.sync()
        self.fake.errors[COMBAT] = NotFoundError(400, "Invalid input.")
        rec = self.sync()
        self.assertEqual(rec["counts"]["DELETED"], 0)
        self.assertFalse(rec["scan_complete"])
        self.assertTrue(any("보류" in w for w in rec["warnings"]))

    # 8
    def test_local_edit_is_conflict(self):
        self.sync()
        path = self.file_of(WORLD)
        path.write_text("내가 고친 내용\n", encoding="utf-8")
        rec = self.sync()  # 원격 변경 없음 + 로컬 수정 → 충돌
        self.assertEqual(items(rec, "CONFLICT"), ["게임 기획서/세계관.md"])
        self.assertEqual(path.read_text(encoding="utf-8"), "내가 고친 내용\n")
        self.fake.edit(WORLD, body=[["bulleted_list", "대륙: 4개"]])
        rec = self.sync()  # 양쪽 다 변경 → 충돌, Notion 최신본은 conflicts 폴더로
        self.assertEqual(items(rec, "CONFLICT"), ["게임 기획서/세계관.md"])
        self.assertEqual(path.read_text(encoding="utf-8"), "내가 고친 내용\n")
        saved = self.project / ".notion-sync" / "conflicts" / rec["run_id"] / "게임 기획서/세계관.md"
        self.assertIn("대륙: 4개", saved.read_text(encoding="utf-8"))
        path.unlink()  # 해결: 로컬 파일 삭제 후 재동기화
        rec = self.sync()
        self.assertEqual(items(rec, "ADDED"), ["게임 기획서/세계관.md"])
        self.assertIn("대륙: 4개", path.read_text(encoding="utf-8"))

    def test_unmanaged_existing_file_is_not_overwritten(self):
        (self.docs / "게임 기획서").mkdir(parents=True)
        (self.docs / "게임 기획서/세계관.md").write_text("손으로 만든 파일\n", encoding="utf-8")
        rec = self.sync()
        self.assertIn("게임 기획서/세계관.md", items(rec, "CONFLICT"))
        self.assertEqual((self.docs / "게임 기획서/세계관.md").read_text(encoding="utf-8"), "손으로 만든 파일\n")

    # 9
    def test_api_error_keeps_existing_file(self):
        self.sync()
        old = self.file_of(COMBAT).read_text(encoding="utf-8")
        self.fake.edit(COMBAT, body=[["text", "새 내용"]])
        self.fake.errors[COMBAT] = NotionError(503, "재시도 4회 실패")
        rec = self.sync()
        self.assertFalse(rec["success"])
        self.assertEqual(self.file_of(COMBAT).read_text(encoding="utf-8"), old)
        self.assertEqual(rec["counts"]["DELETED"], 0)
        self.fake.errors.clear()
        self.assertEqual(items(self.sync(), "MODIFIED"), ["게임 기획서/전투시스템.md"])

    # 10
    def test_interrupted_sync_recovers(self):
        real = storage.write_text
        calls = {"n": 0}

        def crashing(ws, path, text):
            if Path(path).suffix == ".md" and "Docs" in Path(path).parts:
                calls["n"] += 1
                if calls["n"] == 3:
                    raise KeyboardInterrupt  # 프로세스 강제 종료 흉내
            return real(ws, path, text)

        with mock.patch.object(storage, "write_text", crashing):
            with self.assertRaises(KeyboardInterrupt):
                self.sync()
        self.assertFalse((self.project / ".notion-sync" / "sync.lock").exists())
        self.assertEqual(list(self.docs.glob(".*.tmp")), [])
        rec = self.sync()
        self.assertTrue(any("완료되지 않았습니다" in w for w in rec["warnings"]))
        self.assertEqual(rec["counts"]["CONFLICT"], 0)
        self.assertEqual(rec["counts"]["ADDED"], 4)
        self.assertEqual(rec["counts"]["UNCHANGED"], 2)

    def test_concurrent_run_blocked(self):
        state = self.project / ".notion-sync"
        (state / "sync.lock").write_text("123", encoding="utf-8")
        with self.assertRaises(storage.LockError):
            self.sync()

    # 11
    def test_incomplete_page_is_rejected(self):
        self.sync()
        old = self.file_of(COMBAT).read_text(encoding="utf-8")
        self.fake.edit(COMBAT, body=[["text", "x"], ["text", "y"]])
        self.fake.missing.add(f"{COMBAT}-b1")  # 본문 블록 하나를 못 받음
        rec = self.sync()
        self.assertEqual(items(rec, "ERROR"), ["게임 기획서/전투시스템.md"])
        self.assertEqual(self.file_of(COMBAT).read_text(encoding="utf-8"), old)
        self.assertEqual(rec["counts"]["DELETED"], 0)

    def test_unsupported_block_is_explicit(self):
        self.fake.edit(WORLD, body=[["text", "앞"], ["mystery_block", "?"]])
        rec = self.sync()
        self.assertIn("<!-- notion-sync: 지원하지 않는 블록 mystery_block -->",
                      self.file_of(WORLD).read_text(encoding="utf-8"))
        self.assertTrue(any("mystery_block" in w for w in rec["warnings"]))

    # 12
    def test_invalid_paths_abort(self):
        for bad in ("../outside", "Assets/Docs", "/tmp/x", "ProjectSettings", "."):
            cfg = {"docs_dir": bad, "root_pages": [ROOT]}
            (self.project / ".notion-sync" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(storage.PathError, msg=bad):
                self.sync()

    # 13
    def test_unity_files_untouched(self):
        files = {"Assets/Player.cs": "class P {}", "Assets/Player.cs.meta": "guid: 1",
                 "ProjectSettings/ProjectSettings.asset": "x", "Packages/manifest.json": "{}", "CLAUDE.md": "# 원본"}
        for rel, text in files.items():
            (self.project / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.project / rel).write_text(text, encoding="utf-8")

        def tree():
            return {str(p.relative_to(self.project)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in self.project.rglob("*") if p.is_file()
                    and p.relative_to(self.project).parts[0] not in ("Docs", ".notion-sync")}

        before = tree()
        self.sync()
        self.fake.edit(COMBAT, body=[["text", "변경"]], title="전투")
        self.sync()
        self.assertEqual(before, tree())

    def test_images_downloaded_once(self):
        ok, bad = "https://x.y/a.png", "https://x.y/b.jpg"
        self.fake.edit(ROOT, body=[["image", ok], ["image", bad]])
        self.fake.broken.add(bad)
        self.assertEqual(self.sync(dry_run=True)["counts"]["ADDED"], 6)
        self.assertEqual(self.fake.downloads, [])  # diff 모드는 다운로드 안 함
        rec = self.sync()
        assets = sorted((self.docs / "assets").iterdir())
        self.assertEqual([a.read_bytes() for a in assets], [f"IMG:{ok}".encode()])
        root = (self.docs / "게임 기획서.md").read_text(encoding="utf-8")
        self.assertIn(f"![image](<assets/{assets[0].name}>)", root)
        self.assertTrue(any("이미지 다운로드 실패" in w for w in rec["warnings"]))
        self.fake.broken.clear()
        self.fake.downloads.clear()
        self.sync()
        self.assertEqual(self.fake.downloads, [bad])  # 받은 이미지는 다시 받지 않고 실패한 것만 재시도

    def test_diff_mode_writes_nothing(self):
        rec = self.sync(dry_run=True)
        self.assertEqual(rec["counts"]["ADDED"], 6)
        self.assertFalse(self.docs.exists())
        self.assertFalse((self.project / ".notion-sync" / "manifest.json").exists())
        self.assertIn("+", rec["items"][0]["diff"])


if __name__ == "__main__":
    unittest.main()
