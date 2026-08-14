"""컨트롤 — 사이드바 "설정" + 입력창 위 표시 전용 배지 (계획서 2.6절).

**세션 5 실사용 후 개정.** 세션 4~5 에서는 모든 컨트롤을 입력창 위 popover 에
몰아넣었으나 두 가지 이유로 되돌렸다.
1. 대화 중 모델을 바꿀 일이 거의 없다. 가장 좋은 자리를 안 쓰는 컨트롤이 차지했다
2. popover 를 열고 고르고 닫을 때마다 리런이 걸려 체감이 무겁다

이제 **조작은 사이드바 설정**, **입력창 위는 표시 전용**이다.
"""

from __future__ import annotations

import math

import streamlit as st

from gchat import state
from gchat.context import count_excluded, estimate_messages, estimate_tokens
from gchat.models import (
    FAMILY_GEMMA4,
    PURPOSE_CODING,
    PURPOSE_CUSTOM,
    PURPOSE_GENERAL,
    PURPOSES,
    ModelSpec,
    compose_instruction,
    get_model,
    max_request_tokens,
    model_ids,
    purpose_label,
    thinking_label,
)
from gchat.quota import QuotaBook
from gchat.state import Conversation

# 계획서 1.5절 — 턴당 컨텍스트 증가 (사용자 입력 약 200 + 출력)
TOKENS_PER_TURN = 1_750
# 이 값을 넘으면 TPM 대기가 실제로 발생하기 시작한다 (계획서 1.5절, 세션 5 실측 반영)
WAIT_FREE_BUDGET = 11_000

BUDGET_MIN = 1_000
BUDGET_STEP = 500

S_NOTES = "control_notes"


# --- 입력창 위 배지 — 표시 전용 (계획서 2.6.1절) --------------------------------


def render_badge(book: QuotaBook, conv: Conversation) -> None:
    """지금 어떤 모델이 어떤 수준으로 답하는지만 알린다. 조작 요소를 넣지 않는다."""
    spec = state.active_model()
    used = estimate_messages(conv.messages) + estimate_tokens(conv.settings.system_instruction)
    line = (
        f"{spec.label} · {thinking_label(spec.id, conv.settings.thinking_level)}"
        f" · 맥락 {used:,}/{conv.settings.context_budget:,}"
    )

    # 대기는 실제로 예상될 때만 덧붙인다 (계획서 2.3절). 0이면 아무것도 쓰지 않는다.
    wait = book.tracker(spec.id).next_wait_s(min(used, conv.settings.context_budget))
    if wait > 0:
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.caption(line)
        right.caption(f"⏳ 다음 요청까지 약 {math.ceil(wait)}초")
    else:
        st.caption(line)


# --- 사이드바 "설정" (계획서 2.6.1.1절) -------------------------------------------


def render_settings(conv: Conversation) -> None:
    """조작 컨트롤. 사이드바 맨 아래에 접힌 채로 둔다 (세션 6 피드백)."""
    with st.expander("설정", expanded=False):
        spec = state.active_model()
        _render_model(conv)
        _render_thinking(spec, conv)
        if spec.family == FAMILY_GEMMA4:
            _render_budget(spec, conv)
        _render_purpose(spec, conv)


def _render_model(conv: Conversation) -> None:
    st.selectbox(
        "모델",
        options=model_ids(),
        format_func=lambda model_id: get_model(model_id).label,
        key=state.S_UI_MODEL,
        on_change=_on_model_change,
    )


def _on_model_change() -> None:
    """계열이 바뀌면 확인 단계로 넘긴다 (계획서 2.1.1 / 2.6.1.1절).

    단 **현재 대화가 비어 있으면** 확인도 새 대화도 없이 모델만 바꾼다
    (계획서 2.4절 — 빈 대화는 최대 1개).
    """
    chosen = state.selected_model_id()
    current = state.active_model_id()
    if chosen == current:
        return

    conv = state.active_conversation()
    same_family = not state.needs_family_confirmation(current, chosen)

    if same_family or (conv is not None and conv.is_empty):
        state.commit_model_selection(chosen)
        if conv is not None:
            _remember(state.switch_model_in_place(conv, chosen))
        return

    state.set_pending_model(chosen)  # 확정하지 않는다 — 확인 단계로


def _render_thinking(spec: ModelSpec, conv: Conversation) -> None:
    levels = list(spec.thinking_levels)
    current = conv.settings.thinking_level
    index = levels.index(current) if current in levels else 0
    conv.settings.thinking_level = st.radio(
        "응답 모드",
        options=levels,
        index=index,
        format_func=lambda level: thinking_label(spec.id, level),
        key=f"thinking_{conv.id}_{spec.id}",
        horizontal=True,
        help="사고 수준만 응답 성향을 바꿉니다 (계획서 1.2절). 다음 요청부터 적용됩니다.",
    )


def _tokens_per_turn(spec: ModelSpec, purpose: str) -> int:
    """턴당 컨텍스트 증가 추정.

    범용은 계획서 1.5절 값(1,750)을 쓴다 — 세션 6 실측(출력 약 1,300 + 입력 약
    200)과 비슷하다. 코딩은 길이 지시를 주지 않아 출력이 상한까지 가는 일이
    잦으므로 상한 기준으로 잡는다. 그래야 "약 N턴"이 거짓말이 되지 않는다.
    """
    if purpose == PURPOSE_CODING:
        return spec.default_max_output + 200
    return TOKENS_PER_TURN


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

    per_turn = _tokens_per_turn(spec, conv.settings.purpose)
    st.caption(f"약 {max(1, chosen // per_turn)}턴 유지 (턴당 약 {per_turn:,} 토큰)")

    if chosen > WAIT_FREE_BUDGET:
        st.warning(f"{WAIT_FREE_BUDGET:,}부터는 요청 사이에 대기가 생깁니다.", icon="⚠️")

    # 계획서 2.6.1.1절 — 줄일 때만 사전 안내한다.
    if chosen < previous:
        excluded = count_excluded(
            conv.messages, chosen, system_instruction=conv.settings.system_instruction
        )
        if excluded:
            st.info(f"현재 대화에서 {excluded}개 메시지가 컨텍스트에서 제외됩니다.")


def _render_purpose(spec: ModelSpec, conv: Conversation) -> None:
    """용도 프리셋 (세션 6). 커스텀일 때만 인스트럭션 입력칸을 보인다."""
    settings = conv.settings
    purposes = list(PURPOSES)
    index = purposes.index(settings.purpose) if settings.purpose in purposes else 0
    chosen = st.radio(
        "용도",
        options=purposes,
        index=index,
        format_func=purpose_label,
        key=f"purpose_{conv.id}_{spec.id}",
        horizontal=True,
        help="범용·코딩은 문구가 자동으로 들어갑니다. 직접 쓰려면 커스텀을 고르세요.",
    )
    if chosen != settings.purpose:
        state.set_purpose(settings, spec.id, chosen)

    if settings.purpose != PURPOSE_CUSTOM:
        return

    # 커스텀일 때만 노출한다. 나머지는 굳이 보여줄 이유가 없다 (세션 6 피드백).
    widget_key = f"sysinst_{conv.id}_{spec.id}"
    value = st.text_area(
        "시스템 인스트럭션",
        value=settings.system_instruction,
        key=widget_key,
        height=140,
        help="비워두면 사용하지 않습니다. 컨텍스트에 영향이 없어 대화 중 바꿔도 안전합니다.",
    )
    if value != settings.system_instruction:
        settings.system_instruction = value

    st.button(
        "용도 기본 문구 가져오기",
        key=f"reset_sysinst_{conv.id}_{spec.id}",
        help="범용 문구와 이 모델의 길이 지시를 다시 채웁니다.",
        on_click=_fill_default_instruction,
        args=(settings, spec.id, widget_key),
    )


def _fill_default_instruction(settings, model_id: str, widget_key: str) -> None:
    """콜백에서 처리한다. 위젯이 그려진 뒤에는 그 키를 대입할 수 없다."""
    text = compose_instruction(model_id, PURPOSE_GENERAL)
    settings.system_instruction = text
    st.session_state[widget_key] = text


# --- 계열 전환 확인 (계획서 2.1.1절) ------------------------------------------------


def render_family_confirmation(conv: Conversation, *, export_button=None) -> None:
    """본문 위쪽에 띄운다. 사이드바보다 눈에 잘 띄고 3버튼이 들어갈 자리가 넉넉하다.

    두 버튼 모두 **on_click 콜백**으로 처리한다. 확정도 취소도 selectbox 의 키
    (`ui_model_id`)를 되돌려야 하는데, 위젯이 이미 그려진 뒤에 그 키를 대입하면
    StreamlitAPIException 이 난다. 콜백은 다음 실행의 위젯 생성 **전**에 돌아서
    대입이 허용된다.
    """
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
    with save_col:
        if export_button is not None:
            export_button(conv)
        else:
            st.button("Markdown으로 저장", disabled=True, width="stretch")
    go_col.button(
        "저장했음 · 전환",
        type="primary",
        width="stretch",
        on_click=_confirm_family_switch,
        args=(conv, pending, target.label),
    )
    cancel_col.button(
        "취소",
        width="stretch",
        on_click=state.revert_model_selection,  # 드롭다운을 이전 모델로 되돌린다
    )


def _confirm_family_switch(conv: Conversation, model_id: str, label: str) -> None:
    state.commit_model_selection(model_id)
    state.start_conversation(model_id, inherit_from=conv)
    _remember([f"{label}로 전환하고 새 대화를 시작했습니다."])


# --- 안내 문구 전달 -----------------------------------------------------------------


def _remember(notes: list[str]) -> None:
    if notes:
        st.session_state.setdefault(S_NOTES, []).extend(notes)


def drain_notes() -> None:
    """설정 변경으로 생긴 안내를 본문에 한 번 띄운다."""
    for note in st.session_state.pop(S_NOTES, []):
        st.info(note)
