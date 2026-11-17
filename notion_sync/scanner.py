"""공개 링크의 최상위 페이지부터 하위 페이지·데이터베이스 행을 재귀 탐색하고 본문을 Markdown으로 만든다."""
from collections import Counter
from dataclasses import dataclass, field

from . import converter, storage
from .client import NotionError, walk
from .config import norm_id

COLLECTION_TYPES = ("collection_view", "collection_view_page")
# ponytail: Windows 260자 경로 제한 대비. 문서 폴더 기준 경로가 이보다 길면 제목을 짧게 자른다(깊이마다 약 30자씩만 늘어남)
MAX_REL_PATH = 150


@dataclass
class PageInfo:
    id: str
    parent_id: str | None
    title: str
    last_edited: str
    archived: bool
    url: str
    path: str = ""  # 문서 폴더 기준 POSIX 경로. Notion 계층을 따른다: `부모.md` 옆 `부모/` 폴더에 자식
    markdown: str | None = None  # None 이면 error 참고
    error: str = ""
    warnings: list = field(default_factory=list)
    images: dict = field(default_factory=dict)  # assets/ 파일명 -> URL

    @classmethod
    def from_block(cls, b: dict) -> "PageInfo":
        return cls(
            id=norm_id(b["id"]),
            parent_id=b.get("parent_id") if b.get("parent_table") != "space" else None,
            title=converter.plain((b.get("properties") or {}).get("title")).strip() or "Untitled",
            last_edited=converter.iso(b.get("last_edited_time")),
            archived=not b.get("alive", True),
            url=converter.page_url(b["id"]),
        )


@dataclass
class ScanResult:
    pages: dict  # id -> PageInfo
    complete: bool  # False 이면 삭제 판정을 하지 않는다
    warnings: list


def scan(client, root_ids: list[str], id_suffix: bool = False, known: dict | None = None) -> ScanResult:
    """id_suffix 면 모든 파일명에 ID를 붙이고, 아니면 이름이 겹칠 때만 붙인다.
    known = 지난 동기화의 {페이지 ID: 파일}. 다른 페이지가 쓰고 있는 파일명은 피하고(제목 맞바꿈, 삭제 후 같은 제목),
    이번에 못 불러온 페이지로 가는 링크도 기존 위치로 유지한다."""
    result = ScanResult({}, True, [])
    seen: set[str] = set()
    known = known or {}
    owners = {f.casefold(): pid for pid, f in known.items()}

    def pick(folder: str, pid: str, title: str, clash: bool) -> str:
        path = folder + storage.safe_filename(title, pid, id_suffix)
        if clash or storage.reserved(path) or owners.get(path.casefold(), pid) != pid:
            path = folder + storage.safe_filename(title, pid)
        if len(path) > MAX_REL_PATH:
            path = folder + storage.safe_filename(title, pid, max_len=20)
        return path

    def fail(msg: str):
        result.complete = False
        result.warnings.append(msg)

    paths: dict[str, str] = {}  # 불러오기에 실패한 페이지도 위치는 알아야 링크가 흔들리지 않는다
    root_names: set[str] = {converter.IMAGE_DIR}  # 최상위 폴더에서 이미 쓴 이름(소문자, 확장자 제외)

    def visit(pid: str, path: str | None, schema: dict | None = None):
        if pid in seen:
            return
        seen.add(pid)
        if path:
            paths[pid] = path
        try:
            rm = client.load_page(pid)
        except NotionError as e:
            fail(f"페이지 {pid} 를 불러오지 못했습니다(공개 공유 상태인지 확인하세요): {e}")
            return
        blocks = rm["block"]
        info = PageInfo.from_block(blocks[pid])
        if path is None:  # 최상위 페이지: 설정 순서대로 이름을 정하고 겹치면 ID를 붙인다
            path = pick("", pid, info.title,
                        storage.safe_filename(info.title, pid, id_suffix)[:-3].casefold() in root_names)
            root_names.add(path[:-3].casefold())
        info.path = paths[pid] = path
        result.pages[info.id] = info
        if info.archived:
            return
        children, collections = [], {}  # (id, 제목, 행 스키마): 하위 페이지와 데이터베이스 행은 같은 폴더에 들어간다
        for b in walk(pid, blocks):
            if b["id"] != pid and b.get("type") in ("page", "collection_view_page"):  # 전체 페이지 DB는 별도 문서
                children.append((b["id"], converter.plain((b.get("properties") or {}).get("title")).strip()
                                 or "Untitled", None))
            elif b.get("type") in COLLECTION_TYPES and (b["id"] == pid or b.get("type") == "collection_view"):
                coll = load_collection(b, rm)
                if coll:
                    collections[b["id"]] = coll
                    children += [(rid, title, coll["schema"]) for rid, title in coll["rows"]]
        try:
            info.markdown, info.warnings, info.images = converter.render(rm, pid, collections, schema)
        except converter.IncompleteError as e:
            info.error = str(e)
            result.complete = False
        folder = path[:-3] + "/"  # `부모.md` → `부모/`
        base = [storage.safe_filename(t, cid, id_suffix).casefold() for cid, t, _ in children]
        count = Counter(base)
        for (cid, title, row_schema), name in zip(children, base):
            visit(norm_id(cid), pick(folder, norm_id(cid), title, count[name] > 1), row_schema)

    def load_collection(b: dict, rm: dict) -> dict | None:
        fmt = b.get("format") or {}
        cid = (fmt.get("collection_pointer") or {}).get("id") or b.get("collection_id")
        view_ids = b.get("view_ids") or []
        if not cid or not view_ids:
            fail(f"데이터베이스 {b['id']} 정보를 읽지 못했습니다.")
            return None
        try:
            row_ids, complete, qrm = client.query_collection(cid, view_ids[0], b.get("space_id", ""))
            coll = rm.get("collection", {}).get(cid) or client.records("collection", [cid]).get(cid) or {}
        except NotionError as e:
            fail(f"데이터베이스 {b['id']} 조회 실패: {e}")
            return None
        if not complete:
            fail(f"데이터베이스 '{converter.plain(coll.get('name'))}' 결과가 잘렸습니다.")
        row_blocks = qrm.get("block", {})
        rows = [(rid, converter.plain((row_blocks.get(rid, {}).get("properties") or {}).get("title")).strip() or "Untitled")
                for rid in row_ids]
        return {"name": converter.plain(coll.get("name")), "rows": rows, "schema": coll.get("schema") or {}}

    for rid in root_ids:
        visit(norm_id(rid), None)
    links = {**known, **paths}
    for p in result.pages.values():
        if p.markdown is not None:
            p.markdown = converter.resolve_links(p.markdown, p.path, links)
    return result

