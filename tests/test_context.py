"""컨텍스트 절단·토큰 추정 테스트 (계획서 2.2절). 네트워크를 쓰지 않는다."""

from __future__ import annotations

import pytest

from gchat.context import (
    BOUNDARY_RATIO,
    KOREAN_CHARS_PER_TOKEN,
    OTHER_CHARS_PER_TOKEN,
    estimate_messages,
    estimate_tokens,
    fit_to_budget,
    is_cjk,
    needs_exact_count,
    single_input_too_large,
    too_large_message,
)
from gchat.state import Message

GEMMA = "gemma-4-31b-it"
GEMINI = "gemini-3.5-flash-lite"


def user(text: str) -> Message:
    return Message(role="user", content=text)


def model(text: str) -> Message:
    return Message(role="model", content=text)


# --- 상수 (계획서 2.2절 확정치) ---------------------------------------------------


def test_상수는_계획서_2_2절_확정치다():
    assert KOREAN_CHARS_PER_TOKEN == 1.6
    assert OTHER_CHARS_PER_TOKEN == 3.5
    assert BOUNDARY_RATIO == 0.15


# --- 문자 분류 -------------------------------------------------------------------


@pytest.mark.parametrize("ch", ["가", "힣", "한", "ㄱ", "あ", "カ", "漢"])
def test_CJK로_분류되는_문자(ch: str):
    assert is_cjk(ch) is True


@pytest.mark.parametrize("ch", ["a", "Z", "0", " ", ".", "!", "\n", "€"])
def test_CJK가_아닌_문자(ch: str):
    assert is_cjk(ch) is False


# --- 추정 ------------------------------------------------------------------------


def test_빈_문자열은_0이다():
    assert estimate_tokens("") == 0


def test_한국어는_1_6자당_1토큰():
    text = "가" * 160
    assert estimate_tokens(text) == 100


def test_영어는_3_5자당_1토큰():
    text = "a" * 350
    assert estimate_tokens(text) == 100


def test_혼합_입력은_문자_종류별로_나눠_센다():
    """세션 3 결정 — 한쪽 상수로 전체를 세지 않는다."""
    text = "가" * 160 + "a" * 350
    assert estimate_tokens(text) == 200  # 100 + 100

    # 한쪽 상수로 뭉뚱그렸다면 나왔을 값과 달라야 한다
    assert estimate_tokens(text) != len(text) / KOREAN_CHARS_PER_TOKEN
    assert estimate_tokens(text) != len(text) / OTHER_CHARS_PER_TOKEN


def test_코드_블록이_섞인_한국어_질문():
    """실제로 흔한 모양 — 한국어 설명 + 영문 코드.

    추정식을 베껴 확인하는 대신, 두 단일 상수로 뭉뚱그린 값 사이에 들어오는지를 본다.
    영문 코드가 대부분이므로 전체를 한국어 상수로 세면 크게 과대 추정된다.
    """
    text = "다음 함수를 고쳐 주세요.\n" + "def add(a, b):\n    return a + b\n" * 5
    assert any(is_cjk(ch) for ch in text)
    assert any(ch.isalpha() and not is_cjk(ch) for ch in text)

    estimate = estimate_tokens(text)
    all_korean = len(text) / KOREAN_CHARS_PER_TOKEN
    all_other = len(text) / OTHER_CHARS_PER_TOKEN
    assert all_other < estimate < all_korean
    # 영문이 압도적이므로 영어 상수 쪽에 훨씬 가깝다
    assert estimate < (all_korean + all_other) / 2


def test_공백은_직전_문자의_분류를_따른다():
    """공백을 무조건 영어 상수로 세면 한국어 문장을 과소 추정한다.

    "가 가"(3자)를 CJK 3자로 보면 ceil(3/1.6)=2, 공백을 영어로 할인하면
    ceil(2/1.6 + 1/3.5)=2 로 같아 보이지만, 문장이 길어지면 차이가 벌어진다.
    """
    korean = "가 " * 100  # 한글 100 + 공백 100
    assert estimate_tokens(korean) == pytest.approx(125, abs=1)  # 200 / 1.6

    english = "a " * 100
    assert estimate_tokens(english) == pytest.approx(58, abs=1)  # 200 / 3.5


@pytest.mark.parametrize(
    ("text", "measured"),
    [
        ("인버터 용량을 어떻게 정하나요? 태양광 발전소 설계 기준을 알려주세요.", 23),
        ("How do I size an inverter for a solar plant? Explain the DC/AC ratio.", 19),
    ],
)
def test_실측_표본을_과소_추정하지_않는다(text: str, measured: int):
    """세션 2 실측 표본 (docs/archive/api_findings.md A-2).

    과소 추정은 한도를 넘겨 429 를 부르는 위험한 방향이므로, 실측 이상이어야 한다.
    과대도 20% 안쪽이어야 쓸모가 있다.
    """
    estimate = estimate_tokens(text)
    assert measured <= estimate <= measured * 1.2


def test_이력_전체_추정은_메시지_합이다():
    messages = [user("가" * 160), model("a" * 350)]
    assert estimate_messages(messages) == 200


# --- 경계 판정 -------------------------------------------------------------------


def test_예산_경계_15퍼센트_안이면_실호출이_필요하다():
    budget = 3_000
    assert needs_exact_count(3_000, budget) is True
    assert needs_exact_count(2_550, budget) is True  # -15%
    assert needs_exact_count(3_450, budget) is True  # +15%
    assert needs_exact_count(2_549, budget) is False
    assert needs_exact_count(3_451, budget) is False


def test_예산이_0이면_실호출하지_않는다():
    assert needs_exact_count(100, 0) is False


# --- 절단 ------------------------------------------------------------------------


def test_예산_안에_들면_그대로_둔다():
    messages = [user("짧은 질문"), model("짧은 답"), user("또 질문")]
    result = fit_to_budget(messages, 3_000)
    assert result.messages == messages
    assert result.trimmed == 0
    assert result.truncated is False
    assert all(not m.truncated_from_context for m in messages)


def test_오래된_user_model_쌍부터_뺀다():
    messages = [
        user("가" * 1_600),  # 1,000 토큰
        model("가" * 1_600),  # 1,000
        user("가" * 1_600),  # 1,000
        model("가" * 1_600),  # 1,000
        user("가" * 160),  # 100
    ]
    result = fit_to_budget(messages, 2_200)
    assert result.trimmed == 2  # 첫 user/model 쌍
    assert result.messages == messages[2:]
    assert result.tokens == 2_100


def test_절단된_메시지는_삭제하지_않고_표시만_한다():
    """계획서 2.2절 — 화면과 MD 내보내기에는 그대로 남는다."""
    messages = [user("가" * 1_600), model("가" * 1_600), user("나" * 160)]
    result = fit_to_budget(messages, 1_200)

    assert len(messages) == 3  # 원본은 그대로다
    assert messages[0].truncated_from_context is True
    assert messages[1].truncated_from_context is True
    assert messages[2].truncated_from_context is False
    assert result.messages == [messages[2]]
    assert result.trimmed == 2


def test_예산을_다시_늘리면_절단이_풀린다():
    messages = [user("가" * 1_600), model("가" * 1_600), user("나" * 160)]
    fit_to_budget(messages, 1_200)
    assert messages[0].truncated_from_context is True

    result = fit_to_budget(messages, 3_000)
    assert result.trimmed == 0
    assert all(not m.truncated_from_context for m in messages)


def test_마지막_사용자_메시지는_남긴다():
    """혼자서도 예산을 넘는 경우. 호출자가 single_input_too_large 로 미리 막는다."""
    messages = [user("가" * 1_600), model("가" * 1_600), user("가" * 16_000)]
    result = fit_to_budget(messages, 1_000)
    assert result.messages == [messages[2]]
    assert result.trimmed == 2


def test_시스템_인스트럭션은_항상_유지되고_예산에서_먼저_빠진다():
    system = "가" * 1_600  # 1,000 토큰
    messages = [user("가" * 1_600), model("가" * 1_600), user("나" * 160)]

    without = fit_to_budget(messages, 3_000)
    assert without.trimmed == 0

    with_system = fit_to_budget(messages, 3_000, system_instruction=system)
    assert with_system.trimmed == 2  # 남은 예산 2,000 에 맞추느라 쌍이 빠졌다


def test_모델_답이_없는_이력도_처리한다():
    messages = [user("가" * 1_600), user("가" * 1_600), user("나" * 160)]
    result = fit_to_budget(messages, 1_200)
    assert result.messages[-1] is messages[-1]
    assert result.trimmed >= 1


# --- count_tokens 실호출 연동 ------------------------------------------------------


def test_경계_밖에서는_count_tokens를_부르지_않는다():
    calls = []

    def counter(items):
        calls.append(len(items))
        return 999

    messages = [user("가" * 160)]  # 100 토큰, 예산 3,000 → 경계 밖
    result = fit_to_budget(messages, 3_000, count_exact=counter)
    assert calls == []
    assert result.used_exact_count is False
    assert result.tokens == 100


def test_경계_안에서는_count_tokens를_부른다():
    calls = []

    def counter(items):
        calls.append(len(items))
        return 2_900  # 실측은 예산 안

    messages = [user("가" * 4_800)]  # 3,000 토큰 추정 = 예산과 같음 → 경계 안
    result = fit_to_budget(messages, 3_000, count_exact=counter)
    assert calls == [1]
    assert result.used_exact_count is True
    assert result.tokens == 2_900
    assert result.trimmed == 0


def test_실호출_결과가_예산을_넘으면_더_절단한다():
    """추정은 통과했지만 실측이 예산을 넘는 경우."""
    calls = []

    def counter(items):
        calls.append(len(items))
        return 3_400 if len(items) == 3 else 1_000

    messages = [user("가" * 2_560), model("가" * 1_600), user("가" * 640)]
    result = fit_to_budget(messages, 3_000, count_exact=counter)
    assert result.trimmed == 2

    # 절단 후 남은 400 토큰은 경계(3,000 ± 450) 밖이라 다시 부르지 않는다.
    # 실호출은 경계 근처에서만 한다는 계획서 2.2절 규칙이 여기서도 지켜진다.
    assert calls == [3]
    assert result.tokens == 400
    assert result.used_exact_count is False


# --- 단일 입력이 너무 큰 경우 --------------------------------------------------------


def test_단일_입력이_요청당_한도를_넘는지_판정한다():
    """계획서 2.2절 — TPM 의 90%가 요청당 한도다 (Gemma 14,400)."""
    assert single_input_too_large("가" * 100, GEMMA) is False
    assert single_input_too_large("가" * 24_000, GEMMA) is True  # 15,000 토큰
    # 같은 입력도 Gemini 에서는 여유롭다 (225,000)
    assert single_input_too_large("가" * 24_000, GEMINI) is False


def test_안내_문구는_모델_이름과_전환_대상을_담는다():
    message = too_large_message(GEMMA, GEMINI)
    assert "Gemma 4 31B" in message
    assert "Gemini 3.5 Flash-Lite" in message
    assert "너무 깁니다" in message
