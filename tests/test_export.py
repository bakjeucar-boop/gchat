"""Markdown 내보내기 테스트 (계획서 2.5절)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gchat.export import (
    archive_filename,
    conversation_filename,
    fence_for,
    has_content,
    render_archive,
    render_conversation,
    safe_title,
)
from gchat.state import KST, Message, Settings, new_conversation

GEMINI = "gemini-3.5-flash-lite"
GEMMA = "gemma-4-31b-it"
MOMENT = datetime(2026, 8, 13, 14, 30, tzinfo=KST)


def make_conversation(title: str = "인버터 용량 산정") -> object:
    conv = new_conversation(GEMINI)
    conv.title = title
    conv.messages = [
        Message(role="user", content="인버터 용량을 어떻게 정하나요?", created_at=MOMENT),
        Message(
            role="model",
            content="일반적으로 DC/AC 비율을 봅니다.",
            model_id=GEMINI,
            in_tokens=24,
            out_tokens=312,
            latency_s=3.2,
            created_at=MOMENT + timedelta(minutes=1),
        ),
    ]
    return conv


# --- 파일명 (계획서 2.5절) ---------------------------------------------------------


def test_현재_대화_파일명():
    conv = make_conversation()
    assert conversation_filename(conv, MOMENT) == "gchat_20260813_1430_인버터_용량_산정.md"


def test_전체_대화_파일명():
    assert archive_filename(MOMENT) == "gchat_전체_20260813_1430.md"


def test_파일명에_쓸_수_없는_문자를_지운다():
    assert safe_title('보고서: "1/2" <초안>?') == "보고서_12_초안"


def test_제목이_길면_30자로_자른다():
    assert len(safe_title("가" * 60)) == 30


def test_제목이_비면_기본값을_쓴다():
    assert safe_title("///") == "대화"


# --- frontmatter (계획서 2.5절) -----------------------------------------------------


def test_frontmatter에_필요한_항목이_모두_들어간다():
    text = render_conversation(make_conversation(), moment=MOMENT)
    head = text.split("---")[1]
    assert "생성: 2026-08-13 14:30 (KST)" in head
    assert "제목: 인버터 용량 산정" in head
    assert "모델: Gemini 3.5 Flash-Lite" in head
    assert "응답 모드: 빠름 (thinking_level=minimal)" in head
    assert "컨텍스트 예산: 32,000" in head
    assert "메시지 수: 2" in head
    assert "누적 토큰: 입력 24 / 출력 312" in head


def test_메시지_제목줄에_토큰과_지연이_들어간다():
    text = render_conversation(make_conversation(), moment=MOMENT)
    assert "## 사용자 · 14:30" in text
    assert "## Gemini 3.5 Flash-Lite · 14:31 · 입력 24 / 출력 312 토큰 · 3.2초" in text


def test_시스템_인스트럭션은_인용_블록으로_들어간다():
    conv = make_conversation()
    conv.settings = Settings(
        thinking_level="minimal", context_budget=32_000, system_instruction="3문장 이내로 답한다"
    )
    text = render_conversation(conv, moment=MOMENT)
    assert "> 3문장 이내로 답한다" in text


def test_인스트럭션이_없으면_넣지_않는다():
    conv = make_conversation()
    conv.settings = Settings(thinking_level="minimal", context_budget=32_000)
    conv.settings.system_instruction = ""
    assert "시스템 인스트럭션" not in render_conversation(conv, moment=MOMENT)


# --- 코드 블록 (계획서 2.5절) --------------------------------------------------------


def test_코드_블록이_있어도_깨지지_않는다():
    conv = make_conversation()
    conv.messages[1].content = "예시입니다.\n\n```python\nprint('hi')\n```\n\n끝."
    text = render_conversation(conv, moment=MOMENT)
    # 본문을 다시 감싸지 않으므로 원문 그대로 살아 있다
    assert "```python\nprint('hi')\n```" in text
    assert text.count("```") == 2


def test_더_긴_울타리를_고른다():
    assert fence_for("보통 텍스트") == "```"
    assert fence_for("```\ncode\n```") == "````"
    assert fence_for("````\n```\n````") == "`````"


# --- 절단 표시 (계획서 2.5절) --------------------------------------------------------


def test_컨텍스트_절단_지점을_표시한다():
    conv = make_conversation()
    conv.messages[0].truncated_from_context = True
    text = render_conversation(conv, moment=MOMENT)
    assert "> 이 시점 이전 1개 메시지는 컨텍스트에서 제외되었습니다" in text
    # 절단된 메시지도 본문에는 그대로 남는다 (계획서 2.2절)
    assert "인버터 용량을 어떻게 정하나요?" in text


def test_절단이_없으면_표시하지_않는다():
    assert "컨텍스트에서 제외" not in render_conversation(make_conversation(), moment=MOMENT)


def test_출력_잘림도_표시한다():
    conv = make_conversation()
    conv.messages[1].truncated_output = True
    assert "> 출력 한도로 잘린 답변입니다." in render_conversation(conv, moment=MOMENT)


# --- 전체 내보내기 -------------------------------------------------------------------


def test_전체_대화는_구분선으로_이어붙인다():
    first = make_conversation("첫 대화")
    second = make_conversation("둘째 대화")
    text = render_archive([first, second], moment=MOMENT)
    assert "# 첫 대화" in text
    assert "# 둘째 대화" in text
    assert "대화 수: 2" in text
    assert "전체 메시지 수: 4" in text
    assert "\n---\n\n" in text


def test_빈_대화는_버튼을_막는다():
    assert has_content(make_conversation()) is True
    assert has_content(new_conversation(GEMMA)) is False


def test_Gemma_대화도_라벨이_맞는다():
    conv = new_conversation(GEMMA)
    conv.title = "비상용"
    conv.messages = [Message(role="user", content="질문", created_at=MOMENT)]
    text = render_conversation(conv, moment=MOMENT)
    assert "모델: Gemma 4 31B" in text
    assert "응답 모드: 사고 끄기 (thinking_level=minimal)" in text
