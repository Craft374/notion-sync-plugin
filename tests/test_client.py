"""HTTP 계층: urlopen 을 가짜로 바꿔 재시도·오류 분류·청크 이어받기·누락 블록 보충을 검증한다."""
import io
import json
import unittest
import urllib.error

from notion_sync.client import NotFoundError, NotionClient, NotionError


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://www.notion.so", code, "err", headers or {},
                                  io.BytesIO(json.dumps({"message": f"m{code}"}).encode()))


def rec(b):
    return {"value": {"value": b, "role": "reader"}}  # 실제 응답처럼 이중 value


class Script:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout):
        self.requests.append((req.full_url.rsplit("/", 1)[-1], json.loads(req.data)))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(json.dumps(r).encode())


class ClientTest(unittest.TestCase):
    def make(self, *responses):
        self.sleeps = []
        self.opener = Script(*responses)
        return NotionClient(opener=self.opener, sleep=self.sleeps.append, min_interval=0)

    def test_429_respects_retry_after(self):
        c = self.make(http_error(429, {"Retry-After": "7"}), http_error(529), {"recordMap": {}})
        self.assertEqual(c.records("block", ["x"]), {})
        self.assertEqual(self.sleeps, [7.0, 1.0])

    def test_5xx_limited_retries(self):
        c = self.make(*[http_error(500)] * 5)
        with self.assertRaises(NotionError) as cm:
            c.records("block", ["x"])
        self.assertEqual(cm.exception.status, 500)
        self.assertEqual(self.sleeps, [1, 2, 4, 8])

    def test_private_page_is_not_found(self):
        c = self.make(http_error(400))  # 비공개/없는 페이지에 loadPageChunk 는 400
        with self.assertRaises(NotFoundError):
            c.load_page("p")
        self.assertEqual(self.sleeps, [])

    def test_network_error_retried(self):
        c = self.make(urllib.error.URLError("dns"), {"recordMap": {}})
        self.assertEqual(c.records("block", ["x"]), {})

    def test_load_page_follows_chunks_and_fills_missing(self):
        page = {"id": "p", "type": "page", "content": ["a", "b", "child"]}
        c = self.make(
            {"cursor": {"stack": [[{"id": "x"}]]}, "recordMap": {"block": {"p": rec(page), "a": rec({"id": "a", "type": "toggle", "content": ["a1"]})}}},
            {"cursor": {"stack": []}, "recordMap": {"block": {"child": rec({"id": "child", "type": "page", "content": ["deep"]})}}},
            {"recordMap": {"block": {"b": rec({"id": "b", "type": "text"}), "a1": rec({"id": "a1", "type": "text"})}}},
        )
        rm = c.load_page("p")
        self.assertEqual(set(rm["block"]), {"p", "a", "b", "a1", "child"})  # 하위 페이지 내부(deep)는 요청 안 함
        self.assertEqual([r[0] for r in self.opener.requests], ["loadPageChunk", "loadPageChunk", "syncRecordValues"])
        self.assertEqual(self.opener.requests[1][1]["chunkNumber"], 1)

    def test_denied_block_stops_fill_loop(self):
        page = {"id": "p", "type": "page", "content": ["a", "secret"]}
        c = self.make(
            {"cursor": {"stack": []}, "recordMap": {"block": {"p": rec(page)}}},
            {"recordMap": {"block": {"a": rec({"id": "a", "type": "text"})}}},  # secret 은 안 옴
        )
        rm = c.load_page("p")
        self.assertEqual(rm["denied"], ["secret"])
        self.assertEqual(len(self.opener.requests), 2)  # 같은 요청을 반복하지 않음

    def test_query_collection(self):
        c = self.make({"result": {"reducerResults": {"collection_group_results": {"blockIds": ["r1"], "hasMore": True}}},
                       "recordMap": {"block": {"r1": rec({"id": "r1"})}}})
        ids, complete, rm = c.query_collection("c", "v", "s")
        self.assertEqual((ids, complete, list(rm["block"])), (["r1"], False, ["r1"]))


if __name__ == "__main__":
    unittest.main()
