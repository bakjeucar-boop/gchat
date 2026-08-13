"""사이드바 — 휘발성 경고, 대화 목록, 삭제, 한도 표시 (계획서 2.4 / 2.6.3절).

설정 항목은 사이드바에 두지 않는다 (계획서 2.6.3절).
한도 게이지의 시각적 완성은 세션 5이므로 여기서는 숫자만 보여준다.
"""

from __future__ import annotations

import streamlit as st

from gchat import state
from gchat.models import get_model
from gchat.quota import QuotaBook

VOLATILE_WARNING = (
    "⚠️ 대화는 이 세션에서만 유지됩니다. 새로고침하면 사라집니다.\n\n"
    "남기려면 Markdown으로 내려받으세요."
)


def render(book: QuotaBook) -> None:
    with st.sidebar:
        # 계획서 2.4절 — 상단에 고정한다.
        st.warning(VOLATILE_WARNING)

        if st.button("＋ 새 대화", width="stretch"):
            state.clear_deleted()
            current = state.active_conversation()
            state.start_conversation(state.active_model_id(), inherit_from=current)
            st.rerun()

        _render_conversations()
        _render_undo()
        _render_bulk_delete()
        _render_quota(book)

        st.caption("Markdown 내보내기는 세션 6에서 붙습니다.")


def _render_conversations() -> None:
    conversations = state.recent_conversations()
    if not conversations:
        st.caption("대화가 없습니다. 아래 입력창에 질문을 적으면 시작됩니다.")
        return

    st.subheader(f"대화 {len(conversations)}개", divider="gray")
    active_id = state.active_conversation().id if state.active_conversation() else None

    for conv in conversations:
        open_col, edit_col, delete_col = st.columns([6, 1, 1], vertical_alignment="center")
        label = ("● " if conv.id == active_id else "") + conv.title
        if open_col.button(
            label,
            key=f"open_{conv.id}",
            width="stretch",
            help=f"{get_model(conv.model_id).label} · 메시지 {len(conv.messages)}개",
        ):
            state.clear_deleted()
            st.session_state[state.S_ACTIVE_CONVERSATION] = conv.id
            st.rerun()

        with edit_col.popover("✏️", help="제목 변경"):
            new_title = st.text_input(
                "제목", value=conv.title, key=f"title_{conv.id}", max_chars=60
            )
            if st.button("저장", key=f"save_title_{conv.id}"):
                conv.title = new_title.strip() or state.DEFAULT_TITLE
                st.rerun()

        if delete_col.button("🗑", key=f"delete_{conv.id}", help="이 대화 삭제"):
            state.delete_conversation(conv.id)
            st.rerun()


def _render_undo() -> None:
    """되돌리기 (계획서 2.4절).

    계획서 문구는 "되돌리기 5초"지만 Streamlit 은 상호작용이 있어야 화면을
    다시 그리므로 초 단위 자동 소멸을 만들 수 없다. 세션 4 결정에 따라
    **다음 조작까지** 버튼을 유지한다.
    """
    removed = state.deleted_conversation()
    if removed is None:
        return
    if st.button(f"↩️ '{removed.title}' 삭제 취소", width="stretch"):
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


def _render_quota(book: QuotaBook) -> None:
    """한도 잔여량 (계획서 2.3절). 진행바와 배치는 세션 5에서 다듬는다."""
    spec = state.active_model()
    gauges = book.tracker(spec.id).gauges()
    st.subheader("한도", divider="gray")
    st.caption(spec.label)
    st.text(
        f"분당 요청  {gauges.requests_in_window} / {gauges.rpm_limit}\n"
        f"분당 토큰  {gauges.input_tokens_in_window:,} / {gauges.tpm_limit:,}\n"
        f"일일 요청  {gauges.daily_requests} / {gauges.rpd_limit:,}"
    )
    st.caption("분당 토큰은 입력만 셉니다 (계획서 1.4절).")
