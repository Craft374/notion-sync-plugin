"""실제 Notion 없이 엔진을 검증하는 가짜 클라이언트. 공개 링크용 NotionClient 와 같은 메서드와 recordMap 형식을 쓴다."""
import json
from pathlib import Path

from notion_sync.client import NotFoundError, NotionError

FIXTURE = Path(__file__).parent / "fixtures" / "sample_tree.json"
SCHEMA = {"title": {"name": "이름", "type": "title"}, "st": {"name": "상태", "type": "select"}}


def uid(n: int) -> str:
    raw = "a" * 24 + f"{n:08x}"  # 실제 Notion처럼 앞부분은 같고 끝부분만 다르다
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


class FakeNotion:
    def __init__(self, fixture: Path = FIXTURE):
        data = json.loads(fixture.read_text(encoding="utf-8"))
        self.pages = {}
        for p in data["pages"]:
            self.add(uid(p["n"]), p["title"], uid(p["parent"]) if p.get("parent") else None, p["body"],
                     in_db=p.get("in_db", False), props=p.get("props"))
        self.dbs = {uid(d["n"]): {"title": d["title"], "parent": uid(d["parent"])} for d in data["databases"]}
        self.errors = {}  # page_id -> load_page 에서 던질 예외
        self.missing = set()  # load_page 응답에서 뺄 블록 ID(불완전 응답 흉내)
        self.load_calls = []
        self.downloads = []  # 다운로드 요청한 URL
        self.broken = set()  # 다운로드 실패시킬 URL

    # --- 시나리오 조작 ---
    def add(self, pid, title, parent, body, in_db=False, props=None):
        self.pages[pid] = {"title": title, "parent": parent, "body": body, "alive": True,
                           "in_db": in_db, "props": props or {}, "edited": 0}

    def edit(self, pid, body=None, title=None, parent=None):
        p = self.pages[pid]
        p["body"] = body if body is not None else p["body"]
        p["title"] = title if title is not None else p["title"]
        p["parent"] = parent if parent is not None else p["parent"]
        p["edited"] += 1

    # --- recordMap 생성 ---
    def _page_block(self, pid):
        p = self.pages[pid]
        if p["in_db"]:
            parent, table = f"coll-{p['parent']}", "collection"
        else:
            parent, table = (p["parent"], "block") if p["parent"] else ("space", "space")
        props = {"title": [[p["title"]]], **{k: [[v]] for k, v in p["props"].items()}}
        return {"id": pid, "type": "page", "properties": props, "alive": p["alive"], "parent_id": parent,
                "parent_table": table, "last_edited_time": 1793500000000 + p["edited"] * 1000, "content": []}

    def _build(self, spec, bid, blocks):
        kind = spec[0]
        b = {"id": bid, "type": kind, "alive": True}
        if kind == "table":
            rows = spec[1]
            cols = [f"c{i}" for i in range(len(rows[0]))]
            b["format"] = {"table_block_column_order": cols}
            b["content"] = []
            for i, row in enumerate(rows):
                rid = f"{bid}-r{i}"
                blocks[rid] = {"id": rid, "type": "table_row", "alive": True,
                               "properties": {c: [[v]] for c, v in zip(cols, row)}}
                b["content"].append(rid)
        elif kind == "image":
            b["properties"] = {"source": [[spec[1]]]}
        elif kind == "code":
            b["properties"] = {"language": [[spec[1]]], "title": [[spec[2]]]}
        else:
            b["properties"] = {"title": [[spec[1]]]}
            if len(spec) > 2:
                b["content"] = [self._build(c, f"{bid}-{i}", blocks) for i, c in enumerate(spec[2])]
        blocks[bid] = b
        return bid

    def load_page(self, pid):
        self.load_calls.append(pid)
        if pid in self.errors:
            raise self.errors[pid]
        if pid not in self.pages:
            raise NotFoundError(400, "Invalid input.")
        rm = {"block": {}, "collection": {}}
        blocks = rm["block"]
        page = blocks[pid] = self._page_block(pid)
        if not page["alive"]:
            return rm
        page["content"] = [self._build(s, f"{pid}-b{i}", blocks) for i, s in enumerate(self.pages[pid]["body"])]
        for cid, c in self.pages.items():
            if c["parent"] == pid and c["alive"] and not c["in_db"]:
                blocks[cid] = {**self._page_block(cid), "content": ["(하위 페이지 본문은 별도 조회)"]}
                page["content"].append(cid)
        for did, d in self.dbs.items():
            if d["parent"] == pid:
                blocks[did] = {"id": did, "type": "collection_view", "alive": True, "space_id": "space",
                               "view_ids": [f"view-{did}"], "format": {"collection_pointer": {"id": f"coll-{did}"}}}
                rm["collection"][f"coll-{did}"] = {"name": [[d["title"]]], "schema": SCHEMA}
                page["content"].append(did)
        for m in self.missing:
            blocks.pop(m, None)
        return rm

    def records(self, table, ids):
        assert table == "block"
        return {i: self._page_block(i) for i in ids if i in self.pages}

    def query_collection(self, coll_id, view_id, space_id):
        did = coll_id[len("coll-"):]
        rows = [pid for pid, p in self.pages.items() if p["in_db"] and p["parent"] == did and p["alive"]]
        return rows, True, {"block": {r: self._page_block(r) for r in rows}}

    def download(self, url):
        self.downloads.append(url)
        if url in self.broken:
            raise NotionError(404, "이미지 다운로드 실패")
        return f"IMG:{url}".encode()
