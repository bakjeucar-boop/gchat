"""gchat 진입점 (계획서 3절).

세션 4 범위: 인증 게이트 + 사이드바 + 채팅. 컨트롤 바(모델·응답 모드·예산)의
시각적 완성과 계열 전환 확인 절차는 세션 5다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from gchat import state
from gchat.auth import check_password, get_secret
from gchat.client import GchatApiError, GeminiClient
from gchat.quota import QuotaBook
from gchat.ui import chat, controls, export_ui, sidebar

SECRET_KEY_API = "GEMINI_API_KEY"
S_QUOTA_BOOK = "quota_book"

st.set_page_config(page_title="gchat", page_icon="💬", layout="centered")

if not check_password():
    st.stop()

state.init_session_state()


@st.cache_resource(show_spinner=False)
def _build_client(api_key: str) -> GeminiClient:
    """세션마다 클라이언트를 새로 만들 이유가 없다."""
    return GeminiClient(api_key)


def _client() -> GeminiClient | None:
    api_key = get_secret(SECRET_KEY_API)
    if not api_key:
        return None
    try:
        return _build_client(api_key)
    except GchatApiError as exc:
        st.error(str(exc))
        return None


def _quota_book() -> QuotaBook:
    """한도 추적기는 세션 상태에 둔다 (계획서 2.3절 — 세션 안에서만 유지)."""
    if S_QUOTA_BOOK not in st.session_state:
        st.session_state[S_QUOTA_BOOK] = QuotaBook(lambda: datetime.now(UTC))
    return st.session_state[S_QUOTA_BOOK]


book = _quota_book()
client = _client()

st.title("gchat")

if client is None:
    st.warning(
        "API 키가 등록되어 있지 않아 답변을 받을 수 없습니다. 앱 설정(Secrets)에 키를 넣어 주세요."
    )

# 순서가 화면 배치다 (계획서 2.6절).
# 조작은 사이드바 설정, 본문은 대화 이력 → 표시 전용 배지 → 입력창.
# 계열 전환 확인은 사이드바의 모델 드롭다운 바로 아래에서 그린다 — 본문 위쪽에
# 두면 긴 대화에서 화면 밖에 놓여 보이지 않았다 (세션 8 실측).
conversation = state.ensure_conversation()
sidebar.render(
    book,
    conversation,
    export_section=lambda: export_ui.render_sidebar_export(conversation),
    confirm_export=export_ui.render_confirmation_export,
)

controls.drain_notes()
chat.render_history(conversation)
controls.render_badge(book, conversation)
chat.render_input(client, book, conversation)
