"""把 报告.md 转成 HTML 再用 Edge 无头模式打印成 PDF。

不依赖第三方包：自带一个够用的 Markdown 子集解析器（标题/段落/表格/代码块/
列表/引用/图片/行内代码/加粗/链接），并允许直接写原生 HTML（figure/div）。
用法：
    python build_pdf.py
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
MD_FILE = BASE / "报告.md"
HTML_FILE = BASE / "报告.html"
PDF_FILE = BASE / "实验6-工具调用、Agent与Harness实现.pdf"

EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

CSS = """
@page { size: A4; margin: 17mm 15mm 16mm 15mm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
       font-size: 10.5pt; line-height: 1.7; color: #1a1a1a; }
h1 { font-size: 20pt; text-align: center; margin: 0 0 8mm 0; letter-spacing: 1px; }
h2 { font-size: 14pt; color: #1f4e79; border-left: 4px solid #1f4e79;
     padding-left: 8px; margin: 8mm 0 3mm 0; page-break-after: avoid; }
h3 { font-size: 12pt; margin: 5mm 0 2mm 0; page-break-after: avoid; }
p { margin: 2mm 0; text-align: justify; }
table { width: 100%; border-collapse: collapse; font-size: 9pt;
        margin: 3mm 0 4mm 0; page-break-inside: avoid; }
th, td { border: 1px solid #9aa5b1; padding: 3px 6px; vertical-align: top;
         text-align: left; }
th { background: #eef2f7; font-weight: 600; }
th:first-child, td:first-child { white-space: nowrap; }
pre { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 3px;
      padding: 7px 9px; font-family: Consolas, "Courier New", monospace;
      font-size: 8.2pt; line-height: 1.32; white-space: pre; overflow: hidden;
      page-break-inside: avoid; margin: 3mm 0; }
pre.diagram { font-family: "NSimSun", "SimSun", monospace; font-size: 8.2pt;
              line-height: 1.22; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt;
       background: #f0f2f4; padding: 0 2px; border-radius: 2px; }
figure { margin: 4mm 0; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 118mm; border: 1px solid #c8ced4; }
figcaption { font-size: 8.8pt; color: #555; margin-top: 2px; }
.cover { page-break-after: always; text-align: center; margin-top: 26mm; }
.cover p { text-align: center; }
.cover table { width: 70%; margin: 12mm auto 0; font-size: 11pt; }
.cover td { border: 1px solid #9aa5b1; padding: 6px 10px; }
.cover td:first-child { width: 30%; background: #eef2f7; font-weight: 600; }
ul, ol { margin: 2mm 0; padding-left: 7mm; }
li { margin: 1mm 0; padding-left: 1mm; }
th:last-child, td:last-child { white-space: nowrap; }
blockquote { margin: 3mm 0; padding: 2mm 4mm; border-left: 3px solid #c8ced4;
             color: #444; background: #fafafa; }
"""


def _join(lines: list[str]) -> str:
    """把软换行拼成一段：两侧都是中文字符时不插空格。"""
    text = ""
    for line in lines:
        line = line.strip()
        if not text:
            text = line
            continue
        a, b = text[-1], line[0]
        if ord(a) > 0x2E7F or ord(b) > 0x2E7F or a in "，。、；：（）“”‘’《》！？…—":
            text += line
        else:
            text += " " + line
    return text


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img src="\2" alt="\1">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue  # 分隔行
        rows.append(cells)
    if not rows:
        return ""
    out = ["<table>"]
    head, body = rows[0], rows[1:]
    out.append("<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>")
    if body:
        out.append("<tbody>")
        for row in body:
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out)


def convert(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 原生 HTML 块：<figure>…</figure>、<div>…</div>
        if stripped.startswith("<"):
            while i < len(lines) and lines[i].strip():
                out.append(lines[i])
                i += 1
            continue

        if stripped.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(block)
            cls = ' class="diagram"' if "┌" in code else ""
            out.append(f"<pre{cls}>" + html.escape(code) + "</pre>")
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block))
            continue

        if stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + inline(_join(block)) + "</blockquote>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(t)}</li>" for t in items) + "</ul>")
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            out.append("<ol>" + "".join(f"<li>{inline(t)}</li>" for t in items) + "</ol>")
            continue

        # 普通段落：连续的、非空且不以块级标记开头的行
        block = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "|", ">", "```", "<"))
                    or re.match(r"^[-*]\s+", nxt) or re.match(r"^\d+\.\s+", nxt)):
                break
            block.append(nxt)
            i += 1
        out.append(f"<p>{inline(_join(block))}</p>")

    body = "\n".join(out)
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<style>{CSS}</style></head><body>{body}</body></html>"
    )


def main() -> None:
    source = MD_FILE.read_text(encoding="utf-8")
    HTML_FILE.write_text(convert(source), encoding="utf-8")
    print(f"HTML -> {HTML_FILE}")

    if not EDGE.is_file():
        sys.exit(f"找不到 Edge：{EDGE}")

    if PDF_FILE.exists():
        PDF_FILE.unlink()

    profile = BASE / "_edge_profile"
    cmd = [
        str(EDGE), "--headless", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--no-first-run", "--disable-crash-reporter",
        f"--user-data-dir={profile}",
        f"--print-to-pdf={PDF_FILE}",
        HTML_FILE.as_uri(),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        print(proc.stderr[-800:])
        sys.exit(f"Edge 打印失败，返回码 {proc.returncode}")

    size = PDF_FILE.stat().st_size if PDF_FILE.exists() else 0
    print(f"PDF  -> {PDF_FILE} ({size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
