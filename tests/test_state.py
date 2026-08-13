"""대화 관리 순수 로직 테스트 (계획서 2.4절).

session_state 를 건드리는 래퍼는 화면 시험(AppTest)에서 확인하고,
여기서는 streamlit 없이 돌아가는 부분만 본다.
"""

from __future__ import annotations

from datetime import timedelta

from gchat.state import (
    DEFAULT_TITLE,
    TITLE_LIMIT,
    Message,
    Settings,
    new_conversation,
    remove_conversation,
    sort_by_recent,
    title_from_first_message,
)

GEMINI = "gemini-3.5-flash-lite"
GEMMA = "gemma-4-31b-it"


# --- 제목 자동 생성 (계획서 2.4절) -------------------------------------------------


def test_짧은_메시지는_그대로_제목이_된다():
    assert title_from_first_message("인버터 용량 산정") == "인버터 용량 산정"


def test_30자를_넘으면_자른다():
    text = "가" * 50
    title = title_from_first_message(text)
    assert len(title) == TITLE_LIMIT + 1  # 말줄임표 한 글자
    assert title.endswith("…")


def test_줄바꿈과_연속_공백은_한_줄로_눌린다():
    assert title_from_first_message("첫 줄\n\n두 번째   줄") == "첫 줄 두 번째 줄"


def test_빈_입력은_기본_제목을_쓴다():
    assert title_from_first_message("") == DEFAULT_TITLE
    assert title_from_first_message("   \n  ") == DEFAULT_TITLE


def test_경계_길이는_자르지_않는다():
    text = "가" * TITLE_LIMIT
    assert title_from_first_message(text) == text


# --- 목록 정렬·삭제 ----------------------------------------------------------------


def test_사이드바는_최신순이다():
    old = new_conversation(GEMINI)
    new = new_conversation(GEMINI)
    new.updated_at = old.updated_at + timedelta(minutes=5)

    assert sort_by_recent([old, new]) == [new, old]


def test_삭제는_목록에서_빼내_돌려준다():
    first = new_conversation(GEMINI)
    second = new_conversation(GEMINI)
    items = [first, second]

    removed = remove_conversation(items, first.id)
    assert removed is first
    assert items == [second]


def test_없는_대화를_지우면_None():
    items = [new_conversation(GEMINI)]
    assert remove_conversation(items, "없는id") is None
    assert len(items) == 1


# --- 새 대화의 설정 승계 (계획서 2.1.1절) --------------------------------------------


def test_새_대화는_응답_모드와_시스템_인스트럭션을_이어받는다():
    previous = Settings(
        thinking_level="high", context_budget=32_000, system_instruction="3문장 이내로"
    )
    conv = new_conversation(GEMINI, inherit=previous)
    assert conv.settings.thinking_level == "high"
    assert conv.settings.system_instruction == "3문장 이내로"


def test_예산은_새_모델의_기본값으로_초기화된다():
    previous = Settings(thinking_level="minimal", context_budget=32_000)
    conv = new_conversation(GEMMA, inherit=previous)
    assert conv.settings.context_budget == 3_000  # 계획서 1.4절


def test_새_모델에_없는_응답_모드는_minimal로_되돌아간다():
    previous = Settings(thinking_level="medium", context_budget=32_000)
    conv = new_conversation(GEMMA, inherit=previous)
    assert conv.settings.thinking_level == "minimal"


def test_새_대화의_기본_제목():
    assert new_conversation(GEMINI).title == DEFAULT_TITLE


# --- 메시지 부가 필드 (계획서 2.8절) -------------------------------------------------


def test_메시지는_지연시간과_잘림을_담을_수_있다():
    message = Message(role="model", content="답", latency_s=3.2, truncated_output=True)
    assert message.latency_s == 3.2
    assert message.truncated_output is True


def test_기본값은_비어_있다():
    message = Message(role="user", content="질문")
    assert message.latency_s is None
    assert message.truncated_output is False
    assert message.truncated_from_context is False
