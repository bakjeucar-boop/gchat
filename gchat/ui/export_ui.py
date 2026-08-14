"""내보내기 버튼 (계획서 2.5절).

서버에 파일을 쓰지 않는다 — st.download_button 에 문자열을 넘길 뿐이다.
"""

from __future__ import annotations

import streamlit as st

from gchat import state
from gchat.export import (
    archive_filename,
    conversation_filename,
    has_content,
    render_archive,
    render_conversation,
)
from gchat.state import Conversation

MIME = "text/markdown"


def render_sidebar_export(conv: Conversation) -> None:
    """휘발성 경고 바로 아래 (계획서 2.6.3절 7번)."""
    st.download_button(
        "현재 대화 저장",
        data=render_conversation(conv) if has_content(conv) else "",
        file_name=conversation_filename(conv),
        mime=MIME,
        disabled=not has_content(conv),
        width="stretch",
        help="이 대화만 Markdown 으로 내려받습니다.",
    )

    conversations = [c for c in state.recent_conversations() if has_content(c)]
    st.download_button(
        f"전체 대화 저장 ({len(conversations)}개)",
        data=render_archive(conversations) if conversations else "",
        file_name=archive_filename(),
        mime=MIME,
        disabled=not conversations,
        width="stretch",
        help="빈 대화는 빼고 모두 이어붙여 내려받습니다.",
    )


def render_confirmation_export(conv: Conversation) -> None:
    """계열 전환 확인의 [Markdown으로 저장] (계획서 2.1.1절).

    download_button 은 눌러도 페이지가 리런되지만, 확인 단계는 pending_model_id
    로 유지되므로 다운로드 후에도 그대로 남는다.
    """
    st.download_button(
        "Markdown으로 저장",
        data=render_conversation(conv) if has_content(conv) else "",
        file_name=conversation_filename(conv),
        mime=MIME,
        disabled=not has_content(conv),
        width="stretch",
    )
