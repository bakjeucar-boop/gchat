"""컨텍스트 윈도우 관리 (계획서 2.2절).

- 문자 수 기반 1차 추정으로 판정하고, 예산 경계 ±15% 구간에서만 count_tokens 실호출
- 예산을 넘으면 오래된 user/model 쌍부터 컨텍스트에서 뺀다
- 뺀 메시지는 **삭제하지 않는다.** truncated_from_context 만 True 로 표시하고
  화면과 Markdown 내보내기에는 그대로 남는다
- 시스템 인스트럭션은 항상 유지한다 (예산에서 먼저 차감)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gchat.models import get_model, max_request_tokens
from gchat.state import Message

# 계획서 2.2절 확정치 (세션 2 실측: 한국어 1.63~1.70, 영어 3.63).
# 둘 다 실측보다 토큰을 조금 많게 세는 안전한 방향이다.
KOREAN_CHARS_PER_TOKEN = 1.6
OTHER_CHARS_PER_TOKEN = 3.5

# 경계 ±15% 안에서만 실제 count_tokens 를 부른다.
BOUNDARY_RATIO = 0.15

# CJK 계열 코드포인트 구간. 여기에 드는 문자는 1.6자/토큰으로 센다.
_CJK_RANGES = (
    (0x1100, 0x11FF),  # 한글 자모
    (0x3040, 0x30FF),  # 히라가나·가타카나
    (0x3130, 0x318F),  # 한글 호환 자모
    (0x3400, 0x4DBF),  # CJK 확장 A
    (0x4E00, 0x9FFF),  # CJK 통합 한자
    (0xA960, 0xA97F),  # 한글 자모 확장 A
    (0xAC00, 0xD7A3),  # 한글 음절
    (0xD7B0, 0xD7FF),  # 한글 자모 확장 B
    (0xF900, 0xFAFF),  # CJK 호환 한자
    (0xFF00, 0xFFEF),  # 전각 문자
)


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """문자 종류별로 나눠 세고 합산한다 (세션 3 결정).

    한국어 질문에 영어 코드 블록이 섞이는 경우가 흔해서, 한쪽 상수로 전체를
    세면 오차가 크다. CJK 는 1.6자/토큰, 나머지(라틴·숫자·기호)는
    3.5자/토큰으로 각각 세어 더한다.

    **공백은 직전 비공백 문자의 분류를 따른다.** 공백을 무조건 "나머지"로 넣으면
    한국어 문장을 실측보다 낮게 추정한다(세션 2 표본: 실측 23토큰인 문장이 21로
    나왔다). 실제 토크나이저는 공백을 뒤따르는 낱말에 붙여 세므로, 한국어 사이의
    공백을 3.5자/토큰으로 할인하면 안 된다. 과소 추정은 한도를 넘겨 429 를 부르는
    위험한 방향이라 이 규칙을 둔다.
    """
    if not text:
        return 0
    cjk = other = 0
    last_was_cjk: bool | None = None
    pending_space = 0
    for ch in text:
        if ch.isspace():
            if last_was_cjk is None:
                pending_space += 1  # 첫 비공백 문자가 나올 때까지 미뤄 둔다
            elif last_was_cjk:
                cjk += 1
            else:
                other += 1
            continue
        if is_cjk(ch):
            cjk += 1 + pending_space
            last_was_cjk = True
        else:
            other += 1 + pending_space
            last_was_cjk = False
        pending_space = 0
    other += pending_space  # 공백뿐인 문자열
    return math.ceil(cjk / KOREAN_CHARS_PER_TOKEN + other / OTHER_CHARS_PER_TOKEN)


def estimate_messages(messages: Sequence[Message]) -> int:
    """이력 전체의 추정 입력 토큰. 역할 표시 같은 부가 토큰은 세지 않는다."""
    return sum(estimate_tokens(m.content) for m in messages)


def needs_exact_count(estimate: int, budget: int) -> bool:
    """예산 경계 ±15% 안에 들어오면 실제 count_tokens 를 불러야 한다 (계획서 2.2절)."""
    if budget <= 0:
        return False
    return abs(estimate - budget) <= budget * BOUNDARY_RATIO


@dataclass
class TrimResult:
    """절단 결과. UI 는 trimmed 로 "앞선 N개 메시지가 제외됨"을 표시한다."""

    messages: list[Message]  # 컨텍스트로 보낼 메시지
    trimmed: int  # 컨텍스트에서 빠진 메시지 수
    tokens: int  # 보낼 메시지의 토큰 수 (추정 또는 실측)
    used_exact_count: bool = False

    @property
    def truncated(self) -> bool:
        return self.trimmed > 0


def _select_within_budget(
    ordered: list[Message],
    available: int,
    measure: Callable[[list[Message]], tuple[int, bool]],
) -> tuple[list[Message], int, bool]:
    """예산에 들어갈 때까지 앞에서부터 user/model 쌍을 뺀다. 상태를 바꾸지 않는다."""
    kept = ordered[:]
    tokens, exact = measure(kept)
    while tokens > available and len(kept) > 1:
        # 모델 답이 없으면 하나만 뺀다.
        drop = 2 if len(kept) > 2 and kept[1].role == "model" else 1
        kept = kept[drop:]
        tokens, exact = measure(kept)
    return kept, tokens, exact


def count_excluded(
    messages: Sequence[Message], budget: int, *, system_instruction: str = ""
) -> int:
    """이 예산이면 몇 개가 컨텍스트에서 빠지는지만 센다 (계획서 2.6.1절 사전 안내).

    슬라이더를 움직일 때마다 부르므로 절단을 실제로 적용하지 않고
    count_tokens 도 호출하지 않는다. 추정만 쓴다.
    """
    ordered = list(messages)
    available = max(0, budget - estimate_tokens(system_instruction))
    kept, _, _ = _select_within_budget(
        ordered, available, lambda items: (estimate_messages(items), False)
    )
    return len(ordered) - len(kept)


def fit_to_budget(
    messages: Sequence[Message],
    budget: int,
    *,
    system_instruction: str = "",
    count_exact: Callable[[list[Message]], int] | None = None,
) -> TrimResult:
    """예산에 맞을 때까지 오래된 user/model 쌍부터 컨텍스트에서 뺀다.

    - 시스템 인스트럭션은 항상 유지하므로 예산에서 먼저 차감한다
    - 마지막 사용자 메시지는 절대 빼지 않는다. 그것만으로 예산을 넘는 경우는
      호출자가 single_input_too_large 로 미리 막는다 (계획서 2.2절)
    - 원본 Message 의 truncated_from_context 를 매번 다시 세운다.
      예산을 늘리면 이전에 빠졌던 메시지가 되돌아온다
    - count_exact 를 주면 경계 ±15% 구간에서만 호출한다
    """
    ordered = list(messages)
    for message in ordered:
        message.truncated_from_context = False

    available = max(0, budget - estimate_tokens(system_instruction))

    def measure(items: list[Message]) -> tuple[int, bool]:
        estimate = estimate_messages(items)
        if count_exact is not None and needs_exact_count(estimate, available):
            return count_exact(items), True
        return estimate, False

    kept, tokens, exact = _select_within_budget(ordered, available, measure)
    trimmed_messages = ordered[: len(ordered) - len(kept)]
    for message in trimmed_messages:
        message.truncated_from_context = True

    return TrimResult(
        messages=kept,
        trimmed=len(trimmed_messages),
        tokens=tokens,
        used_exact_count=exact,
    )


def single_input_too_large(text: str, model_id: str) -> bool:
    """사용자 입력 하나만으로 요청당 한도를 넘는가 (계획서 2.2절).

    한도는 TPM 의 90%다. TPM 은 입력만 세므로 출력 예상치는 넣지 않는다.
    """
    return estimate_tokens(text) > max_request_tokens(model_id)


def too_large_message(model_id: str, fallback_model_id: str) -> str:
    spec = get_model(model_id)
    fallback = get_model(fallback_model_id)
    return (
        f"이 입력은 {spec.label}의 요청당 한도"
        f"({max_request_tokens(model_id):,} 토큰)를 넘습니다. "
        f"{fallback.label}로 전환하세요."
    )
