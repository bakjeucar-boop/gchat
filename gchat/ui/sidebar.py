"""사이드바.

세션 6 실사용 피드백으로 순서를 정했다 (계획서 2.6.3절과 다르다).

1. 새 대화  2. 대화 목록  3. Markdown 내보내기  4. 전체 삭제
5. 한도 게이지  6. 설정 (접힘)

휘발성 경고는 사용자 요청으로 화면에서 내렸다. 계획서 2.4절은 상시 노출을
요구하므로 되살릴 때를 위해 문구 상수만 남겨 둔다.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from gchat import state
from gchat.models import FAMILY_GEMMA4, get_model
from gchat.quota import QuotaBook
from gchat.state import Conversation
from gchat.ui import controls

# 계획서 2.4절은 휘발성 경고를 상시 노출하라고 하지만, 세션 6 실사용에서
# 빼 달라는 요청을 받아 화면에서는 내렸다. 문구는 되살릴 때를 위해 남겨 둔다.
VOLATILE_WARNING = (
    "⚠️ 대화는 이 세션에서만 유지됩니다. 새로고침하면 사라집니다.\n\n"
    "남기려면 아래에서 Markdown으로 내려받으세요."
)

S_EDITING_TITLE = "editing_title_id"


def render(
    book: QuotaBook,
    conv: Conversation,
    *,
    export_section: Callable[[], None] | None = None,
) -> None:
    """세션 6 실사용 피드백으로 순서를 바꿨다.

    새 대화 → 대화 목록 → 내보내기 2종 → 전체 삭제 → 한도 → 설정(접힘).
    계획서 2.6.3절과 다르며, 휘발성 경고도 사용자 요청으로 뺐다.
    """
    with st.sidebar:
        _render_new_conversation()
        _render_conversations()
        _render_undo()
        if export_section is not None:
            export_section()
        _render_bulk_delete()
        _render_quota(book)
        controls.render_settings(conv)


def _render_new_conversation() -> None:
    if st.button("＋ 새 대화", width="stretch"):
        state.clear_deleted()
        current = state.active_conversation()
        # 이미 빈 대화면 새로 만들지 않는다 (계획서 2.4절 — 빈 대화는 최대 1개).
        if current is None or not current.is_empty:
            state.start_conversation(state.active_model_id(), inherit_from=current)
        st.rerun()


def _render_conversations() -> None:
    conversations = state.recent_conversations()
    if not conversations:
        st.caption("대화가 없습니다. 아래 입력창에 질문을 적으면 시작됩니다.")
        return

    st.subheader(f"대화 {len(conversations)}개", divider="gray")
    active_id = state.active_conversation().id if state.active_conversation() else None

    for conv in conversations:
        # 아이콘 칸을 좁게 잡으면 버튼 상자가 이모지를 다 담지 못해 밖으로 삐져
        # 나온다 (세션 6 피드백). 여유를 주고 두 버튼 다 칸을 채우게 한다.
        open_col, edit_col, delete_col = st.columns([5, 1.4, 1.4], vertical_alignment="center")
        marker = "● " if conv.id == active_id else ""
        # 계획서 2.4절 — 목록은 훑어보는 곳이므로 작은 글씨로, 전체 제목은 툴팁으로.
        tooltip = (
            f"{conv.title}\n\n{get_model(conv.model_id).label} · 메시지 {len(conv.messages)}개"
        )
        if open_col.button(
            f"{marker}{conv.display_title()}",
            key=f"open_{conv.id}",
            width="stretch",
            help=tooltip,
        ):
            state.clear_deleted()
            st.session_state[state.S_ACTIVE_CONVERSATION] = conv.id
            st.rerun()

        # popover 는 라벨 옆에 아래꺾쇠가 붙어 보기 나빴다. 눌러서 펼치는
        # 인라인 편집으로 바꾼다 (세션 6 피드백).
        if edit_col.button("✏️", key=f"edit_{conv.id}", help="제목 변경", width="stretch"):
            st.session_state[S_EDITING_TITLE] = (
                None if st.session_state.get(S_EDITING_TITLE) == conv.id else conv.id
            )
            st.rerun()

        if delete_col.button("🗑", key=f"delete_{conv.id}", help="이 대화 삭제", width="stretch"):
            state.delete_conversation(conv.id)
            st.rerun()

        if st.session_state.get(S_EDITING_TITLE) == conv.id:
            _render_title_editor(conv)


def _render_title_editor(conv) -> None:
    new_title = st.text_input(
        "제목",
        value=conv.title,
        key=f"title_{conv.id}",
        max_chars=60,
        label_visibility="collapsed",
    )
    save_col, cancel_col = st.columns(2)
    if save_col.button("저장", key=f"save_title_{conv.id}", width="stretch"):
        state.rename_conversation(conv, new_title)
        st.session_state[S_EDITING_TITLE] = None
        st.rerun()
    if cancel_col.button("취소", key=f"cancel_title_{conv.id}", width="stretch"):
        st.session_state[S_EDITING_TITLE] = None
        st.rerun()


def _render_undo() -> None:
    """되돌리기 (계획서 2.4절).

    계획서 문구는 당초 "5초"였으나 Streamlit 에는 시간 기반 자동 소멸 수단이
    없어 **다음 조작까지** 유지한다 (세션 4 조정).
    """
    removed = state.deleted_conversation()
    if removed is None:
        return
    if st.button(f"↩️ '{removed.display_title()}' 삭제 취소", width="stretch"):
        state.restore_deleted()
        st.rerun()


def _render_bulk_delete() -> None:
    """일괄 삭제 — 2단계 확인, 되돌리기 없음 (계획서 2.4절)."""
    if not state.conversations():
        return
    with st.expander("전체 삭제"):
        confirmed = st.checkbox("모든 대화를 지웁니다. 되돌릴 수 없습니다.", key="bulk_confirm")
        if st.button("전체 삭제", disabled=not confirmed, type="primary"):
            count = state.delete_all_conversations()
            # 체크박스를 여기서 되돌리지 않는다. 위젯이 만들어진 뒤 그 키를 고치면
            # StreamlitAPIException 이 난다. 대화가 사라지면 이 expander 자체가
            # 그려지지 않으므로 체크 상태도 함께 버려진다.
            st.toast(f"대화 {count}개를 지웠습니다.")
            st.rerun()


def _ratio(used: int, limit: int) -> float:
    if limit <= 0:
        return 0.0
    return min(1.0, used / limit)


def _compact(value: int) -> str:
    return f"{value / 1000:.0f}K" if value >= 10_000 else f"{value:,}"


def _render_quota(book: QuotaBook) -> None:
    """한도 잔여량 진행바 3종 (계획서 2.3절).

    Gemma 에서는 TPM 게이지를 가장 위에 두고 라벨을 강조한다. Streamlit 진행바는
    높이를 바꿀 수 없어 "가장 크게"는 순서와 강조로만 표현한다.
    """
    spec = state.active_model()
    gauges = book.tracker(spec.id).gauges()
    st.subheader("한도", divider="gray")
    st.caption(spec.label)

    tpm_text = f"{_compact(gauges.input_tokens_in_window)} / {_compact(gauges.tpm_limit)}"
    tpm_label = (
        f"**분당 글자** {tpm_text}" if spec.family == FAMILY_GEMMA4 else f"분당 글자 {tpm_text}"
    )
    bars = [
        (tpm_label, _ratio(gauges.input_tokens_in_window, gauges.tpm_limit)),
        (
            f"분당 요청 {gauges.requests_in_window} / {gauges.rpm_limit}",
            _ratio(gauges.requests_in_window, gauges.rpm_limit),
        ),
        (
            f"일일 요청 {gauges.daily_requests:,} / {gauges.rpd_limit:,}",
            _ratio(gauges.daily_requests, gauges.rpd_limit),
        ),
    ]
    if spec.family != FAMILY_GEMMA4:
        # Gemini 는 TPM 이 병목이 아니므로 요청 게이지를 위에 둔다.
        bars = [bars[1], bars[0], bars[2]]

    for label, value in bars:
        st.progress(value, text=label)

    st.caption(
        "질문과 지난 대화가 길수록 '분당 글자'가 빨리 찹니다. "
        "여기 숫자는 앱이 세는 추정치라 실제와 조금 다를 수 있습니다."
    )
