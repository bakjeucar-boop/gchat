"""gchat 진입점 (계획서 3절).

세션 1 범위: 인증 게이트 + 모델 선택 + "준비됨". 실제 API 호출은 세션 2부터다.
"""

from __future__ import annotations

import streamlit as st

from gchat.auth import check_password, get_secret
from gchat.models import get_model, model_ids
from gchat.state import S_UI_MODEL, active_model, commit_model_selection, init_session_state

SECRET_KEY_API = "GEMINI_API_KEY"

st.set_page_config(page_title="gchat", page_icon="💬", layout="centered")

if not check_password():
    st.stop()

init_session_state()

st.title("gchat")


def _on_model_change() -> None:
    """세션 1에서는 대화가 없으므로 선택을 곧바로 확정한다.

    계열 전환 확인 절차(계획서 2.1.1절)는 세션 5의 ui/controls.py 에서 붙인다.
    그때를 위해 확정값(active_model_id)과 UI 선택값은 이미 분리해 두었다.
    """
    commit_model_selection(st.session_state[S_UI_MODEL])


st.selectbox(
    "모델",
    options=model_ids(),
    format_func=lambda model_id: get_model(model_id).label,
    key=S_UI_MODEL,
    on_change=_on_model_change,
)

if not get_secret(SECRET_KEY_API):
    st.warning(
        f"`{SECRET_KEY_API}` 가 설정되어 있지 않습니다. "
        "`.streamlit/secrets.toml` 에 API 키를 넣어야 세션 2부터의 응답 생성이 동작합니다. "
        "지금은 화면 구성만 확인할 수 있습니다."
    )

spec = active_model()
st.success("준비됨")
st.caption(
    f"{spec.label} · 기본 응답 모드 {spec.default_thinking_level} · "
    f"컨텍스트 예산 {spec.default_context_budget:,} · "
    f"한도 RPM {spec.limits.rpm} / TPM {spec.limits.tpm:,} / RPD {spec.limits.rpd:,}"
)
