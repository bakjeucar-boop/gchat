"""기술서 Markdown 을 단일 파일 HTML 로 만든다.

    python scripts/build_docs.py            # docs/gchat_기술서.html 갱신
    python scripts/build_docs.py --check    # 갱신이 필요한지만 확인 (0/1 반환)

원본은 언제나 docs/gchat_기술서.md 다. HTML 은 생성물이며 손으로 고치지 않는다.

**단일 파일 자체 완결형이다.** CSS 를 문서 안에 넣고 외부 링크를 걸지 않는다.
인터넷 없이 열려야 하고, 파일 하나만 보내면 그대로 읽혀야 한다.

**출력은 결정론적이다.** 같은 md 에서는 언제나 바이트가 같은 HTML 이 나온다.
그래야 tests/test_docs.py 가 "md 를 고치고 HTML 을 다시 안 만든" 상태를 잡을 수 있다.
git 에서 읽는 커밋 정보만 예외이며, 그 줄에는 data-volatile 표시를 달아 비교에서
제외한다 (기술서 11.4절).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "gchat_기술서.md"
TARGET = ROOT / "docs" / "gchat_기술서.html"

TITLE = "gchat 기술서"

# 한글 본문은 줄 간격이 넉넉해야 읽힌다. 표가 많아 가로 스크롤도 필요하다.
CSS = """
:root {
  --text: #1a1a1a;
  --muted: #5f6368;
  --line: #d8dade;
  --bg: #ffffff;
  --soft: #f6f7f9;
  --accent: #1a4f8a;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2.5rem 1.25rem 6rem;
  background: var(--bg);
  color: var(--text);
  font-family: "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo",
               "Noto Sans KR", system-ui, sans-serif;
  font-size: 16px;
  line-height: 1.85;
  word-break: keep-all;
  overflow-wrap: anywhere;
}
main { max-width: 52rem; margin: 0 auto; }
h1, h2, h3 { line-height: 1.4; word-break: keep-all; }
h1 { font-size: 2rem; margin: 0 0 .5rem; }
h2 {
  font-size: 1.45rem;
  margin: 3.5rem 0 1rem;
  padding-top: .75rem;
  border-top: 2px solid var(--line);
}
h3 { font-size: 1.12rem; margin: 2.25rem 0 .75rem; color: var(--accent); }
p { margin: 0 0 1rem; }
ul, ol { margin: 0 0 1rem; padding-left: 1.4rem; }
li { margin: .3rem 0; }
strong { font-weight: 700; }
code {
  font-family: Consolas, "D2Coding", "Courier New", monospace;
  font-size: .9em;
  background: var(--soft);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: .05em .35em;
  word-break: break-all;
}
pre {
  background: var(--soft);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: .9rem 1rem;
  overflow-x: auto;
  line-height: 1.55;
}
pre code { background: none; border: 0; padding: 0; font-size: .875rem; word-break: normal; }
blockquote {
  margin: 0 0 1rem;
  padding: .75rem 1rem;
  border-left: 4px solid var(--accent);
  background: var(--soft);
  color: var(--muted);
}
blockquote p:last-child { margin-bottom: 0; }
hr { border: 0; border-top: 1px solid var(--line); margin: 2.5rem 0; }
.table-wrap { overflow-x: auto; margin: 0 0 1.25rem; }
table { border-collapse: collapse; width: 100%; font-size: .93rem; }
th, td {
  border: 1px solid var(--line);
  padding: .5rem .7rem;
  text-align: left;
  vertical-align: top;
  line-height: 1.6;
}
th { background: var(--soft); font-weight: 700; white-space: nowrap; }
a { color: var(--accent); }
.notice {
  background: #fff8e1;
  border: 1px solid #e6c65c;
  border-radius: 6px;
  padding: .9rem 1.1rem;
  margin: 0 0 2rem;
  font-size: .95rem;
}
.notice code { background: #fff; }
#toc {
  background: var(--soft);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 1rem 1.25rem 1.25rem;
}
#toc h2 { font-size: 1.1rem; margin: 0 0 .5rem; padding: 0; border: 0; }
#toc ul { list-style: none; padding-left: 0; margin: 0; }
#toc ul ul { padding-left: 1.1rem; margin: .2rem 0 .6rem; }
#toc li { margin: .15rem 0; }
#toc a { text-decoration: none; }
#toc a:hover { text-decoration: underline; }
#toc .sub { font-size: .92rem; color: var(--muted); }
footer {
  max-width: 52rem;
  margin: 4rem auto 0;
  padding-top: 1rem;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: .85rem;
}
footer p { margin: .25rem 0; }
@media print {
  body { padding: 0; font-size: 11pt; line-height: 1.6; }
  h2 { page-break-after: avoid; }
  h3 { page-break-after: avoid; }
  pre, table, blockquote { page-break-inside: avoid; }
  #toc { page-break-after: always; }
  .notice { border-color: #999; background: none; }
  a { color: inherit; text-decoration: none; }
}
"""

NOTICE = (
    '<div class="notice"><strong>이 파일은 생성물입니다.</strong> 고치려면 '
    "<code>docs/gchat_기술서.md</code>를 고치고 "
    "<code>python scripts/build_docs.py</code>를 다시 실행하세요.</div>"
)


def heading_id(level: int, text: str) -> str:
    """제목에서 안정적인 앵커 id 를 만든다.

    번호를 쓰므로 문장을 다듬어도 링크가 깨지지 않는다.
    """
    plain = re.sub(r"<[^>]+>", "", text).strip()
    if m := re.match(r"^(\d+)장", plain):
        return f"ch-{m.group(1)}"
    if m := re.match(r"^부록\s+([A-Z])", plain):
        return f"ap-{m.group(1).lower()}"
    if m := re.match(r"^(\d+)\.(\d+)", plain):
        return f"s-{m.group(1)}-{m.group(2)}"
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", plain).strip("-").lower()
    return slug or f"h{level}"


def render_body(markdown_text: str) -> tuple[str, list[tuple[int, str, str]]]:
    """본문 HTML 과 (수준, id, 제목) 목록을 돌려준다."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    html = md.render(markdown_text)

    headings: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}

    def tag_heading(match: re.Match[str]) -> str:
        level = int(match.group(1))
        inner = match.group(2)
        base = heading_id(level, inner)
        seen[base] = seen.get(base, 0) + 1
        anchor = base if seen[base] == 1 else f"{base}-{seen[base]}"
        if level in (2, 3):
            headings.append((level, anchor, re.sub(r"<[^>]+>", "", inner).strip()))
        return f'<h{level} id="{anchor}">{inner}</h{level}>'

    html = re.sub(r"<h([1-6])>(.*?)</h\1>", tag_heading, html, flags=re.S)
    # 표는 좁은 화면에서 가로로 넘칠 수 있다. 본문 대신 표만 스크롤하게 감싼다.
    html = html.replace("<table>", '<div class="table-wrap"><table>')
    html = html.replace("</table>", "</table></div>")
    return html, headings


def render_toc(headings: list[tuple[int, str, str]]) -> str:
    lines = ['<nav id="toc"><h2>목차</h2><ul>']
    open_sub = False
    for level, anchor, text in headings:
        if level == 2:
            if open_sub:
                lines.append("</ul></li>")
                open_sub = False
            lines.append(f'<li><a href="#{anchor}">{text}</a>')
            lines.append("<ul>")
            open_sub = True
        else:
            lines.append(f'<li class="sub"><a href="#{anchor}">{text}</a></li>')
    if open_sub:
        lines.append("</ul></li>")
    lines.append("</ul></nav>")
    return "\n".join(lines)


def git_line(path: Path) -> str:
    """원본 md 의 마지막 커밋. 없으면 그 사실을 적는다.

    이 값만 실행 환경에 따라 달라지므로 data-volatile 로 표시해 드리프트 검사에서
    제외한다.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H %cI", "--", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        info = out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        info = ""
    if not info:
        return '<p data-volatile="1">원본 커밋: 확인할 수 없음 (git 정보 없음)</p>'
    commit, _, when = info.partition(" ")
    return f'<p data-volatile="1">원본 커밋: <code>{commit[:12]}</code> · 커밋 시각 {when}</p>'


def build() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    body, headings = render_body(text)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{TITLE}</title>
<style>{CSS}</style>
</head>
<body>
<main>
{NOTICE}
{render_toc(headings)}
{body}</main>
<footer>
<p>원본: <code>docs/gchat_기술서.md</code> · 내용 해시 <code>{digest}</code></p>
{git_line(SOURCE)}
<p>이 HTML 은 <code>scripts/build_docs.py</code> 가 만든 생성물입니다.</p>
</footer>
</body>
</html>
"""


def strip_volatile(html: str) -> str:
    """실행마다 달라질 수 있는 줄을 뺀다 (드리프트 비교용)."""
    return "\n".join(line for line in html.splitlines() if "data-volatile" not in line)


def main(argv: list[str]) -> int:
    if not SOURCE.exists():
        print(f"원본이 없습니다: {SOURCE}")
        return 1
    fresh = build()
    if "--check" in argv:
        if not TARGET.exists():
            print("HTML 이 아직 없습니다. python scripts/build_docs.py 를 실행하세요.")
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if strip_volatile(current) != strip_volatile(fresh):
            print("HTML 이 원본과 어긋납니다. python scripts/build_docs.py 를 실행하세요.")
            return 1
        print("HTML 이 원본과 일치합니다.")
        return 0
    TARGET.write_text(fresh, encoding="utf-8")
    print(f"{TARGET.relative_to(ROOT)} 를 만들었습니다 ({len(fresh):,} 바이트)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
