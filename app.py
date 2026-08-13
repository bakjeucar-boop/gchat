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
from gchat.ui import chat, controls, sidebar

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

sidebar.render(book)

st.title("gchat")

if client is None:
    st.warning(
        f"`{SECRET_KEY_API}` 가 설정되어 있지 않습니다. "
        "`.streamlit/secrets.toml` 에 API 키를 넣어야 응답 생성이 동작합니다."
    )

# 순서가 화면 배치다 — 대화 이력, 그 아래 컨트롤 바, 맨 아래 입력창.
conversation = state.ensure_conversation()
chat.render_history(conversation)
controls.render(book, conversation)
chat.render_input(client, book, conversation)
