"""대화 관리 순수 로직 테스트 (계획서 2.4절).

session_state 를 건드리는 래퍼는 화면 시험(AppTest)에서 확인하고,
여기서는 streamlit 없이 돌아가는 부분만 본다.
"""

from __future__ import annotations

from datetime import timedelta

from gchat.models import (
    GEMMA_DEFAULT_INSTRUCTION,
    PURPOSE_CODING,
    PURPOSE_CUSTOM,
    PURPOSE_GENERAL,
    PURPOSE_INSTRUCTIONS,
)
from gchat.state import (
    DEFAULT_TITLE,
    TITLE_LIMIT,
    Message,
    Settings,
    adopt_model,
    new_conversation,
    remove_conversation,
    set_purpose,
    shorten_title,
    sort_by_recent,
    title_from_first_message,
)

GEMINI = "gemini-3.5-flash-lite"
GEMMA = "gemma-4-31b-it"


# --- 제목 자동 생성 (계획서 2.4절) -------------------------------------------------


def test_짧은_메시지는_그대로_제목이_된다():
    assert title_from_first_message("인버터 용량 산정") == "인버터 용량 산정"


def test_제목은_자르지_않고_보관한다():
    """자르는 것은 표시 단계의 몫이다. 툴팁에 전체를 보여줘야 한다 (계획서 2.4절)."""
    text = "가" * 50
    assert title_from_first_message(text) == text


def test_목록_표시용으로_20자에서_줄인다():
    assert TITLE_LIMIT == 20
    shortened = shorten_title("가" * 50)
    assert len(shortened) == TITLE_LIMIT + 1  # 말줄임표 한 글자
    assert shortened.endswith("…")
    assert shorten_title("짧은 제목") == "짧은 제목"


def test_줄바꿈과_연속_공백은_한_줄로_눌린다():
    assert title_from_first_message("첫 줄\n\n두 번째   줄") == "첫 줄 두 번째 줄"


def test_빈_입력은_기본_제목을_쓴다():
    assert title_from_first_message("") == DEFAULT_TITLE
    assert title_from_first_message("   \n  ") == DEFAULT_TITLE


def test_경계_길이는_줄이지_않는다():
    text = "가" * TITLE_LIMIT
    assert shorten_title(text) == text


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


def test_새_대화는_응답_모드를_이어받는다():
    previous = Settings(thinking_level="high", context_budget=32_000)
    conv = new_conversation(GEMINI, inherit=previous)
    assert conv.settings.thinking_level == "high"


def test_커스텀_인스트럭션은_이어받는다():
    """계획서 2.6.2절 — 커스텀이면 모델을 바꿔도 덮어쓰지 않는다."""
    previous = Settings(
        thinking_level="minimal",
        context_budget=32_000,
        system_instruction="3문장 이내로",
        purpose=PURPOSE_CUSTOM,
    )
    conv = new_conversation(GEMMA, inherit=previous)
    assert conv.settings.system_instruction == "3문장 이내로"
    assert conv.settings.purpose == PURPOSE_CUSTOM
    assert conv.settings.system_instruction_customized is True


def test_커스텀이_아니면_새_모델에_맞게_다시_조합한다():
    """용도 문구는 유지하고 모델별 길이 지시만 갈아끼운다 (세션 6)."""
    previous = Settings(thinking_level="minimal", context_budget=32_000)
    conv = new_conversation(GEMMA, inherit=previous)
    assert conv.settings.purpose == PURPOSE_GENERAL
    assert GEMMA_DEFAULT_INSTRUCTION in conv.settings.system_instruction
    assert PURPOSE_INSTRUCTIONS[PURPOSE_GENERAL] in conv.settings.system_instruction

    # 반대 방향 — Gemini 에는 길이 지시가 없다
    back = new_conversation(GEMINI, inherit=conv.settings)
    assert GEMMA_DEFAULT_INSTRUCTION not in back.settings.system_instruction
    assert PURPOSE_INSTRUCTIONS[PURPOSE_GENERAL] in back.settings.system_instruction


def test_새_대화는_범용_용도로_시작한다():
    gemini = new_conversation(GEMINI).settings
    assert gemini.purpose == PURPOSE_GENERAL
    assert gemini.system_instruction == PURPOSE_INSTRUCTIONS[PURPOSE_GENERAL]

    gemma = new_conversation(GEMMA).settings
    assert PURPOSE_INSTRUCTIONS[PURPOSE_GENERAL] in gemma.system_instruction
    assert GEMMA_DEFAULT_INSTRUCTION in gemma.system_instruction


def test_용도를_바꾸면_문구가_다시_조합된다():
    settings = new_conversation(GEMMA).settings
    set_purpose(settings, GEMMA, PURPOSE_CODING)
    assert PURPOSE_INSTRUCTIONS[PURPOSE_CODING] in settings.system_instruction
    assert GEMMA_DEFAULT_INSTRUCTION in settings.system_instruction  # 길이 지시는 남는다


def test_커스텀으로_바꾸면_지금_문구를_이어서_고칠_수_있다():
    settings = new_conversation(GEMMA).settings
    before = settings.system_instruction
    set_purpose(settings, GEMMA, PURPOSE_CUSTOM)
    assert settings.system_instruction == before  # 빈 칸으로 만들지 않는다
    assert settings.system_instruction_customized is True


# --- 계열 내 모델 전환 (계획서 2.6.1절) ---------------------------------------------


def test_계열_내_전환은_지원하지_않는_응답_모드를_되돌린다():
    settings = Settings(thinking_level="medium", context_budget=9_000)
    notes = adopt_model(settings, GEMMA)
    assert settings.thinking_level == "minimal"
    assert notes and "되돌렸습니다" in notes[0]


def test_계열_내_전환은_예산을_건드리지_않는다():
    """이력을 유지하므로 절단이 생기면 안 된다 (계획서 2.6.1절)."""
    settings = Settings(thinking_level="minimal", context_budget=5_000)
    adopt_model(settings, GEMMA)
    assert settings.context_budget == 5_000


def test_전환_시_커스텀이_아니면_새_모델에_맞게_다시_조합한다():
    settings = Settings(thinking_level="minimal", context_budget=32_000, system_instruction="")
    notes = adopt_model(settings, GEMMA)
    assert GEMMA_DEFAULT_INSTRUCTION in settings.system_instruction
    assert any("맞췄습니다" in note for note in notes)


def test_전환_시_커스텀_인스트럭션은_유지된다():
    settings = Settings(
        thinking_level="minimal",
        context_budget=32_000,
        system_instruction="내 지시",
        purpose=PURPOSE_CUSTOM,
    )
    adopt_model(settings, GEMMA)
    assert settings.system_instruction == "내 지시"


def test_예산은_새_모델의_기본값으로_초기화된다():
    previous = Settings(thinking_level="minimal", context_budget=32_000)
    conv = new_conversation(GEMMA, inherit=previous)
    assert conv.settings.context_budget == 9_000  # 계획서 1.4절 (세션 4 이후 상향)


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
