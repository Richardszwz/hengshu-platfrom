"""USER_GUIDE.md → docx spec JSON（docx skill 的 docx_create.py 输入）。"""
import json
import re
from pathlib import Path

MD = Path(r"D:\hengshu\docs\USER_GUIDE.md")
ASSETS = MD.parent / "assets"


def inline_runs(text: str) -> list[dict]:
    """**粗体** 与 `代码` 行内解析成 runs。"""
    runs = []
    pos = 0
    for m in re.finditer(r'\*\*(.+?)\*\*|`([^`]+)`', text):
        if m.start() > pos:
            runs.append({"text": text[pos:m.start()]})
        if m.group(1) is not None:
            runs.append({"text": m.group(1), "bold": True})
        else:
            runs.append({"text": m.group(2), "italic": True})
        pos = m.end()
    if pos < len(text):
        runs.append({"text": text[pos:]})
    return runs or [{"text": text}]


def split_row(ln: str) -> list[str]:
    """表格行按 | 拆分，尊重 \\| 转义。"""
    s = ln.strip().strip("|")
    cells, cur, esc = [], [], False
    for ch in s:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
            cur.append(ch)
        elif ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur).strip())
    return [c.replace("\\|", "|") for c in cells]


def parse(md: str) -> list[dict]:
    blocks: list[dict] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        # 代码块
        if ln.strip().startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append({"type": "paragraph",
                           "runs": [{"text": "\n".join(buf)}], "style": "CodeBlock"})
            continue
        # 表格
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i + 1]):
            header = split_row(ln)
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = split_row(lines[i])
                # 列数对齐（防爆）
                cells = (cells + [""] * len(header))[: len(header)]
                rows.append([re.sub(r'\*\*(.+?)\*\*', r'\1', c) for c in cells])
                i += 1
            blocks.append({"type": "table", "header": header, "rows": rows, "style": "Table Grid"})
            continue
        # 图片
        m = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$', ln.strip())
        if m:
            p = (MD.parent / m.group(2)).resolve()
            blocks.append({"type": "image", "path": str(p), "width_mm": 165})
            if m.group(1):
                blocks.append({"type": "paragraph",
                               "runs": [{"text": f"▲ {m.group(1)}", "italic": True}], "style": "FigCaption"})
            i += 1
            continue
        # 标题
        m = re.match(r'^(#{1,4})\s+(.*)$', ln)
        if m:
            blocks.append({"type": "heading", "level": len(m.group(1)),
                           "text": re.sub(r'[*`]', '', m.group(2))})
            i += 1
            continue
        # 引用
        if ln.strip().startswith(">"):
            blocks.append({"type": "paragraph",
                           "runs": inline_runs(ln.strip().lstrip(">").strip()), "style": "NoteQuote"})
            i += 1
            continue
        # 列表
        if re.match(r'^\s*-\s+', ln):
            items = []
            while i < len(lines) and re.match(r'^\s*-\s+', lines[i]):
                items.append(re.sub(r'[*`]', '', re.sub(r'^\s*-\s+', '', lines[i])))
                i += 1
            blocks.append({"type": "bullet_list", "items": items})
            continue
        if re.match(r'^\s*\d+\.\s+', ln):
            items = []
            while i < len(lines) and re.match(r'^\s*\d+\.\s+', lines[i]):
                items.append(re.sub(r'[*`]', '', re.sub(r'^\s*\d+\.\s+', '', lines[i])))
                i += 1
            blocks.append({"type": "numbered_list", "items": items})
            continue
        # 分隔线/空行
        if ln.strip() in ("---", ""):
            i += 1
            continue
        # 普通段落
        blocks.append({"type": "paragraph", "runs": inline_runs(ln.strip())})
        i += 1
    return blocks


md = MD.read_text(encoding="utf-8")
print("read ok", len(md), flush=True)
body = parse(md)
print("parse ok", len(body), flush=True)

spec = {
    "page": {"size": "A4", "margins_mm": {"top": 22, "bottom": 20, "left": 22, "right": 22}},
    "footer_page_numbers": True,
    "styles": [
        {"name": "CodeBlock", "font": "Consolas", "size_pt": 8.5, "color": "1a3a4a"},
        {"name": "FigCaption", "font": "Microsoft YaHei UI", "size_pt": 9, "italic": True, "color": "5a7a94"},
        {"name": "NoteQuote", "font": "Microsoft YaHei UI", "size_pt": 10, "italic": True, "color": "8a6d1f"},
    ],
    "blocks": [
        {"type": "paragraph", "runs": [{"text": ""}]},
        {"type": "heading", "level": 0, "text": "衡枢 HENGSHU"},
        {"type": "paragraph", "runs": [{"text": "万流归一，衡以分之", "italic": True}]},
        {"type": "heading", "level": 1, "text": "用户指南 User Guide"},
        {"type": "paragraph", "runs": [
            {"text": "文档版本 v1.0 ｜ 对应平台版本 v1.0.0 ｜ 2026-10-21", "bold": True}]},
        {"type": "paragraph", "runs": [{"text": "多机推理 API 负载均衡平台 + HSE 智能路由引擎——部署 · 使用 · 运维全流程"}]},
        {"type": "page_break"},
        {"type": "toc"},
        {"type": "page_break"},
        *body,
    ],
}

out = Path(r"C:\Users\Richard\AppData\Local\hermes\cache\scratch\guide_spec.json")
out.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
print("spec blocks:", len(body), "→", out)
