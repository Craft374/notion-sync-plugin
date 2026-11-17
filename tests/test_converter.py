import unittest

from notion_sync.converter import IncompleteError, render, resolve_links, rich

PID = "aaaaaaaa-aaaa-aaaa-aaaa-000000000001"


def page(*children, **blocks):
    rm = {"block": {PID: {"id": PID, "type": "page", "properties": {"title": [["문서"]]},
                          "content": [c["id"] for c in children]}}}
    for c in children:
        rm["block"][c["id"]] = {"alive": True, **c}
    rm["block"].update(blocks)
    return rm


def blk(i, type_, title=None, **extra):
    b = {"id": f"b{i}", "type": type_, **extra}
    if title is not None:
        b.setdefault("properties", {})["title"] = title if isinstance(title, list) else [[title]]
    return b


class ConverterTest(unittest.TestCase):
    def test_rich_text(self):
        rt = [["굵게", [["b"]]], [" "], ["링크", [["a", "https://x.y"]]], [" "], ["코드", [["c"]]],
              [" "], ["‣", [["p", "40da56ac-5c28-8125-97d3-d7255d60d7c3"]]], [" "],
              ["‣", [["d", {"type": "date", "start_date": "2026-11-04"}]]], [" "], ["기울임취소", [["i"], ["s"]]]]
        self.assertEqual(resolve_links(rich(rt), "a.md", {}), "**굵게** [링크](https://x.y) `코드` "
                         "[페이지](<https://www.notion.so/40da56ac5c28812597d3d7255d60d7c3>) 2026-11-04 ~~*기울임취소*~~")

    def test_links_are_relative_to_own_folder(self):
        target = "40da56ac-5c28-8125-97d3-d7255d60d7c3"
        md = f"[x](<notion-sync://page/{target}>) [y](notion-sync://page/{target}) ![i](<notion-sync://asset/a.png>)"
        self.assertEqual(resolve_links(md, "루트/하위/문서.md", {target: "루트/다른 문서.md"}),
                         "[x](<../다른 문서.md>) [y](<../다른 문서.md>) ![i](<../../assets/a.png>)")
        self.assertEqual(resolve_links(f"[x](<notion-sync://page/{target}>) `notion-sync://asset/a.png`", "a.md",
                                       {target: "C# 100%/b.md"}),
                         "[x](<C%23 100%25/b.md>) `notion-sync://asset/a.png`")

    def test_blocks(self):
        md, warns, imgs = render(page(
            blk(1, "header", "제목1"), blk(2, "sub_sub_header", "제목3"),
            blk(3, "bulleted_list", "a"), blk(4, "bulleted_list", "b", content=["b4c"]),
            blk(5, "numbered_list", "하나"), blk(6, "to_do", "할일", properties={"title": [["할일"]], "checked": [["Yes"]]}),
            blk(7, "callout", "주의", format={"page_icon": "💡"}), blk(8, "quote", "인용"),
            blk(9, "divider"), blk(10, "code", "x = 1", properties={"title": [["x = 1"]], "language": [["Python"]]}),
            blk(11, "image", None, properties={"source": [["attachment:abc:img.png"]]}),
            blk(12, "bookmark", None, properties={"link": [["https://unity.com"]]}),
            blk(13, "text", ""), blk(14, "column_list", None, content=["b14c"]),
            b4c={"id": "b4c", "type": "bulleted_list", "alive": True, "properties": {"title": [["중첩"]]}},
            b14c={"id": "b14c", "type": "text", "alive": True, "properties": {"title": [["컬럼 안"]]}},
        ), PID)
        md = resolve_links(md, "문서.md", {})
        self.assertEqual(warns, [])
        for want in ("# 문서", "## 제목1", "#### 제목3", "- a\n- b\n  - 중첩", "1. 하나", "- [x] 할일",
                     "> 💡 주의", "> 인용", "---", "```python\nx = 1\n```", "![image](<assets/",
                     "[🔗 https://unity.com](https://unity.com)", "컬럼 안"):
            self.assertIn(want, md)
        self.assertNotIn("\n\n\n", md)
        [(name, url)] = imgs.items()
        self.assertTrue(name.endswith(".png") and f"assets/{name}" in md)
        self.assertEqual(url, "https://www.notion.so/image/attachment%3Aabc%3Aimg.png?table=block&id=b11")

    def test_table_escapes_pipes(self):
        md, *_ = render(page(
            blk(1, "table", None, format={"table_block_column_order": ["x", "y"]}, content=["r1", "r2"]),
            r1={"id": "r1", "type": "table_row", "properties": {"x": [["a"]], "y": [["b"]]}},
            r2={"id": "r2", "type": "table_row", "properties": {"x": [["1|2"]]}},
        ), PID)
        self.assertIn("| a | b |\n|---|---|\n| 1\\|2 |  |", md)

    def test_denied_block_is_placeholder(self):
        rm = page(blk(1, "text", "a"))
        rm["block"][PID]["content"].append("secret")
        rm["denied"] = ["secret"]
        md, warns, _ = render(rm, PID)
        self.assertIn("<!-- notion-sync: 접근할 수 없는 블록 secret -->", md)
        self.assertTrue(warns)

    def test_missing_block_raises(self):
        rm = page(blk(1, "text", "a"))
        rm["block"][PID]["content"].append("gone")
        with self.assertRaises(IncompleteError):
            render(rm, PID)


if __name__ == "__main__":
    unittest.main()
