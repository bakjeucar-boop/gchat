"""문서가 서로 어긋나는 것을 막는다 (기술서 11.4절).

이 프로젝트는 문서 간 동기화 실패를 여러 번 겪었다 (기술서 8.6절). 원본을 하나로
줄인 뒤에도 **파생물이 뒤처지는** 문제는 남는다. 그 둘을 여기서 잡는다.

- `docs/gchat_기술서.html` 은 md 에서 생성된다. md 만 고치고 다시 만들지 않으면 실패
- `CLAUDE.md` 는 기술서 부록 C 에 사본이 있다. 둘이 갈라지면 실패
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

SOURCE = ROOT / "docs" / "gchat_기술서.md"
TARGET = ROOT / "docs" / "gchat_기술서.html"
CLAUDE = ROOT / "CLAUDE.md"

START = "<!-- CLAUDE-MD-START -->"
END = "<!-- CLAUDE-MD-END -->"


def test_기술서_원본이_있다():
    assert SOURCE.exists(), "docs/gchat_기술서.md 가 없다"


def test_HTML이_원본과_어긋나지_않는다():
    """md 를 고치고 build_docs.py 를 안 돌린 상태를 잡는다.

    생성 시각을 넣지 않아 출력이 결정론적이다. git 에서 읽는 커밋 정보만
    실행 환경에 따라 달라지므로 data-volatile 표시를 달아 두었고, 그 줄만 뺀다.
    """
    build_docs = pytest.importorskip("build_docs", reason="markdown-it-py 가 필요하다")
    assert TARGET.exists(), "HTML 이 없다. python scripts/build_docs.py 를 실행할 것"

    fresh = build_docs.build()
    current = TARGET.read_text(encoding="utf-8")
    assert build_docs.strip_volatile(current) == build_docs.strip_volatile(fresh), (
        "기술서 HTML 이 md 와 어긋난다. python scripts/build_docs.py 를 실행할 것"
    )


def _appendix_copy() -> str:
    text = SOURCE.read_text(encoding="utf-8")
    body = text[text.index(START) + len(START) : text.index(END)]
    fence = "````markdown"
    inner = body[body.index(fence) + len(fence) : body.rindex("````")]
    return inner.strip("\n")


def test_부록C_사본이_CLAUDE_md와_같다():
    """CLAUDE.md 는 기술서에서 파생된다. 사본이 뒤처지면 잡는다."""
    assert _appendix_copy() == CLAUDE.read_text(encoding="utf-8").rstrip()


def test_CLAUDE_md가_파생임을_밝힌다():
    """원본이 무엇인지 첫머리에 적혀 있어야 한다 (기술서 0.2절)."""
    head = CLAUDE.read_text(encoding="utf-8")[:400]
    assert "docs/gchat_기술서.md" in head
    assert "요약" in head


def test_동결_문서는_archive에_있다():
    """계획서와 관측 원본은 동결돼 archive 로 옮겼다 (기술서 0.2절)."""
    archive = ROOT / "docs" / "archive"
    assert (archive / "gchat_계획서.md").exists()
    assert (archive / "api_findings.md").exists()
    assert not (ROOT / "docs" / "api_findings.md").exists()
