"""컨트롤 바 — 입력창 바로 위 (계획서 2.6.1 / 2.6.2절).

한 줄 배지를 상시 띄우고 클릭하면 popover 로 펼친다. 별도 설정 메뉴는 두지
않는다 (계획서 2.6절 결정).

계획서 2.6.1절 예시의 오른쪽 "다음 요청까지 대기 없음"은 2.3절 개정과
어긋나므로 2.3절을 따른다 — **대기가 0이면 아무것도 표시하지 않는다.**
"""

from __future__ import annotations

import math

import streamlit as st

from gchat import state
from gchat.context import count_excluded, estimate_messages, estimate_tokens
from gchat.models import (
    FAMILY_GEMMA4,
    ModelSpec,
    get_model,
    max_request_tokens,
    model_ids,
    thinking_label,
)
from gchat.quota import QuotaBook
from gchat.state import Conversation

# 계획서 1.5절 — 턴당 컨텍스트 증가 (사용자 입력 약 200 + 출력 1,536)
TOKENS_PER_TURN = 1_750
# 이 값을 넘으면 TPM 대기가 실제로 발생하기 시작한다 (계획서 1.5절 표)
WAIT_FREE_BUDGET = 12_000

BUDGET_MIN = 1_000
BUDGET_STEP = 500

S_NOTES = "control_notes"


def render(book: QuotaBook, conv: Conversation) -> None:
    _render_family_confirmation(conv)
    _drain_notes()

    spec = state.active_model()
    badge_col, status_col = st.columns([3, 2], vertical_alignment="center")

    with badge_col, st.popover(_badge_label(spec, conv), width="stretch"):
        _render_model(conv)
        _render_thinking(spec, conv)
        if spec.family == FAMILY_GEMMA4:
            _render_budget(spec, conv)
        _render_system_instruction(spec, conv)

    with status_col:
        _render_status(book, spec, conv)


def _badge_label(spec: ModelSpec, conv: Conversation) -> str:
    bits = [spec.label, thinking_label(spec.id, conv.settings.thinking_level)]
    if spec.family == FAMILY_GEMMA4:
        bits.append(f"맥락 {conv.settings.context_budget:,}")
    return " · ".join(bits)


def _render_status(book: QuotaBook, spec: ModelSpec, conv: Conversation) -> None:
    """오른쪽 상태 — 맥락 사용량과, 대기가 있을 때만 대기 예고."""
    used = estimate_messages(conv.messages) + estimate_tokens(conv.settings.system_instruction)
    st.caption(f"맥락 {used:,} / {conv.settings.context_budget:,}")

    # 계획서 2.3절 — 대기가 0이면 아무것도 띄우지 않는다.
    wait = book.tracker(spec.id).next_wait_s(min(used, conv.settings.context_budget))
    if wait > 0:
        st.caption(f"⏳ 다음 요청까지 약 {math.ceil(wait)}초")


# --- popover 안 -----------------------------------------------------------------


def _render_model(conv: Conversation) -> None:
    st.selectbox(
        "모델",
        options=model_ids(),
        format_func=lambda model_id: get_model(model_id).label,
        key=state.S_UI_MODEL,
        on_change=_on_model_change,
    )


def _on_model_change() -> None:
    """계열이 바뀌면 확인 단계로 넘긴다. 계열 안이면 즉시 적용한다 (계획서 2.1.1절)."""
    chosen = state.selected_model_id()
    current = state.active_model_id()
    if chosen == current:
        return
    if state.needs_family_confirmation(current, chosen):
        state.set_pending_model(chosen)  # 확정하지 않는다
        return
    state.commit_model_selection(chosen)
    conv = state.active_conversation()
    if conv is not None:
        conv.model_id = chosen
        _remember(state.adopt_model(conv.settings, chosen))


def _render_thinking(spec: ModelSpec, conv: Conversation) -> None:
    levels = list(spec.thinking_levels)
    current = conv.settings.thinking_level
    index = levels.index(current) if current in levels else 0
    chosen = st.radio(
        "응답 모드",
        options=levels,
        index=index,
        format_func=lambda level: thinking_label(spec.id, level),
        key=f"thinking_{conv.id}_{spec.id}",
        horizontal=True,
        help="사고 수준만 응답 성향을 바꿉니다 (계획서 1.2절). 다음 요청부터 적용됩니다.",
    )
    # 변경은 세션을 초기화하지 않는다 (계획서 2.6.1절).
    conv.settings.thinking_level = chosen


def _render_budget(spec: ModelSpec, conv: Conversation) -> None:
    """Gemma 일 때만. 슬라이더 옆에는 대기 시간이 아니라 **턴 수**를 보인다."""
    ceiling = max_request_tokens(spec.id)  # TPM 의 90% — 테이블에서 유도한다
    previous = conv.settings.context_budget
    chosen = st.slider(
        "컨텍스트 예산",
        min_value=BUDGET_MIN,
        max_value=ceiling,
        value=min(previous, ceiling),
        step=BUDGET_STEP,
        key=f"budget_{conv.id}_{spec.id}",
        help=f"{spec.label}의 요청당 한도는 {ceiling:,} 토큰입니다 (TPM의 90%).",
    )
    conv.settings.context_budget = chosen

    st.caption(f"약 {max(1, chosen // TOKENS_PER_TURN)}턴 유지 (턴당 약 {TOKENS_PER_TURN:,} 토큰)")

    if chosen > WAIT_FREE_BUDGET:
        st.warning(f"{WAIT_FREE_BUDGET:,}부터는 요청 사이에 대기가 생깁니다.", icon="⚠️")

    # 계획서 2.6.1절 — 줄일 때만 사전 안내한다.
    if chosen < previous:
        excluded = count_excluded(
            conv.messages, chosen, system_instruction=conv.settings.system_instruction
        )
        if excluded:
            st.info(f"현재 대화에서 {excluded}개 메시지가 컨텍스트에서 제외됩니다.")


def _render_system_instruction(spec: ModelSpec, conv: Conversation) -> None:
    """모델 기본값을 화면에 그대로 채워 보인다 (계획서 2.6.2절).

    숨겨서 몰래 덧붙이면 사용자가 "왜 이렇게 답하지?"를 추적할 수 없다.
    """
    settings = conv.settings
    value = st.text_area(
        "시스템 인스트럭션",
        value=settings.system_instruction,
        key=f"sysinst_{conv.id}_{spec.id}",
        height=110,
        help="비워두면 사용하지 않습니다. 컨텍스트에 영향이 없어 대화 중 바꿔도 안전합니다.",
    )
    if value != settings.system_instruction:
        settings.system_instruction = value
        # 모델 기본값과 같아지면 다시 기본값을 따라간다.
        settings.system_instruction_customized = value != spec.default_system_instruction

    if settings.system_instruction_customized and spec.default_system_instruction:
        if st.button("모델 기본값으로 되돌리기", key=f"reset_sysinst_{conv.id}_{spec.id}"):
            settings.system_instruction = spec.default_system_instruction
            settings.system_instruction_customized = False
            st.rerun()
    elif spec.default_system_instruction:
        st.caption("이 모델의 기본값입니다. 고치면 모델을 바꿔도 유지됩니다.")


# --- 계열 전환 확인 (계획서 2.1.1절) ------------------------------------------------


def _render_family_confirmation(conv: Conversation) -> None:
    pending = state.pending_model_id()
    if pending is None:
        return
    target = get_model(pending)
    st.warning(
        f"**모델 계열을 바꾸면 새 대화가 시작됩니다.**\n\n"
        f"현재 대화 {len(conv.messages)}개 메시지는 새 대화로 이어지지 않습니다.\n\n"
        f"대화 내용은 이 세션 안에서만 보관되므로, 필요하면 지금 Markdown으로 "
        f"내려받으세요. (전환해도 이전 대화는 사이드바에 남습니다)"
    )
    save_col, go_col, cancel_col = st.columns(3)
    save_col.button(
        "Markdown으로 저장",
        disabled=True,
        width="stretch",
        help="내보내기는 세션 6에서 붙습니다.",
    )
    if go_col.button("저장했음 · 전환", type="primary", width="stretch"):
        state.commit_model_selection(pending)
        state.start_conversation(pending, inherit_from=conv)
        _remember([f"{target.label}로 전환하고 새 대화를 시작했습니다."])
        st.rerun()
    if cancel_col.button("취소", width="stretch"):
        # 드롭다운을 이전 모델로 되돌린다 (계획서 2.1.1절 구현 주의).
        state.revert_model_selection()
        st.rerun()


# --- 안내 문구 전달 -----------------------------------------------------------------


def _remember(notes: list[str]) -> None:
    if notes:
        st.session_state.setdefault(S_NOTES, []).extend(notes)


def _drain_notes() -> None:
    for note in st.session_state.pop(S_NOTES, []):
        st.info(note)
