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
from gchat.models import get_model, model_ids
from gchat.quota import QuotaBook
from gchat.state import S_UI_MODEL
from gchat.ui import chat, sidebar

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


def _on_model_change() -> None:
    """계열이 바뀌면 새 대화를 시작한다 (계획서 2.1.1절).

    세션 4 결정: 확인 단계(저장·전환·취소 3버튼)는 세션 5에서 붙인다. 지금은
    자동으로 새 대화를 열고 안내만 낸다. 이전 대화는 지우지 않고 사이드바에 남는다.
    """
    chosen = st.session_state[S_UI_MODEL]
    previous = state.active_model_id()
    changed_family = state.needs_family_confirmation(previous, chosen)
    state.commit_model_selection(chosen)
    if changed_family:
        current = state.active_conversation()
        state.start_conversation(chosen, inherit_from=current)
        st.session_state["family_switch_note"] = (
            f"{get_model(previous).label} → {get_model(chosen).label} 로 계열이 바뀌어 "
            "새 대화를 시작했습니다. 이전 대화는 사이드바에 남아 있습니다."
        )


book = _quota_book()
client = _client()

sidebar.render(book)

st.title("gchat")
st.selectbox(
    "모델",
    options=model_ids(),
    format_func=lambda model_id: get_model(model_id).label,
    key=S_UI_MODEL,
    on_change=_on_model_change,
)

note = st.session_state.pop("family_switch_note", None)
if note:
    st.info(note)

if client is None:
    st.warning(
        f"`{SECRET_KEY_API}` 가 설정되어 있지 않습니다. "
        "`.streamlit/secrets.toml` 에 API 키를 넣어야 응답 생성이 동작합니다."
    )

chat.render(client, book)
