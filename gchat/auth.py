"""비밀번호 게이트 (계획서 2.7절).

이건 암호화가 아니라 문 잠금이다. 목적은 API 키 남용으로 인한 과금 방지이며
그 목적에는 충분하다.

반드시 지킬 것
- hmac.compare_digest 사용 (== 금지 — 타이밍 공격 방어)
- 입력받은 비밀번호를 session_state 에 저장하지 않는다 (인증 플래그만)
- 비밀번호는 st.secrets 에만 둔다. 저장소에 커밋하지 않는다
- 세션당 실패 5회 시 잠금
"""

from __future__ import annotations

import hmac

import streamlit as st

from gchat.state import S_AUTHED

MAX_ATTEMPTS = 5

S_FAILED_ATTEMPTS = "auth_failed_attempts"

SECRET_KEY_PASSWORD = "APP_PASSWORD"


# --- 순수 로직 (streamlit 없이 테스트 가능) -----------------------------------


def verify_password(entered: str, expected: str) -> bool:
    """비밀번호 일치 여부. 항상 상수 시간 비교를 쓴다.

    빈 입력은 비교하지 않고 곧바로 거절한다. 설정된 비밀번호가 비어 있는
    경우에도 통과시키지 않는다.
    """
    if not entered or not expected:
        return False
    # compare_digest 는 비 ASCII str 을 거부하므로 바이트로 비교한다.
    return hmac.compare_digest(entered.encode("utf-8"), expected.encode("utf-8"))


def is_locked_out(failed_attempts: int) -> bool:
    """실패 횟수가 상한에 도달했는가 (계획서 2.7절 — 세션당 5회)."""
    return failed_attempts >= MAX_ATTEMPTS


def attempts_remaining(failed_attempts: int) -> int:
    return max(0, MAX_ATTEMPTS - failed_attempts)


# --- streamlit 연동 -----------------------------------------------------------


def get_secret(name: str) -> str | None:
    """secrets 에서 값을 읽는다. secrets.toml 자체가 없어도 예외를 내지 않는다."""
    try:
        value = st.secrets[name]
    except Exception:
        return None
    return str(value) if value else None


def check_password() -> bool:
    """인증 게이트. 통과하면 True, 아니면 화면에 입력란을 그리고 False."""
    if st.session_state.get(S_AUTHED):
        return True

    expected = get_secret(SECRET_KEY_PASSWORD)
    if not expected:
        st.error(
            f"`{SECRET_KEY_PASSWORD}` 가 설정되어 있지 않습니다. "
            "`.streamlit/secrets.toml` 에 20자 이상의 무작위 비밀번호를 넣어 주세요. "
            "`.streamlit/secrets.toml.example` 을 참고하세요."
        )
        return False

    failed = st.session_state.get(S_FAILED_ATTEMPTS, 0)
    if is_locked_out(failed):
        st.error(
            f"비밀번호를 {MAX_ATTEMPTS}회 틀려 이 세션은 잠겼습니다. "
            "브라우저를 새로고침하면 다시 시도할 수 있습니다."
        )
        return False

    # form 을 쓰는 이유: key 없는 text_input 은 리런마다 같은 값을 되돌려주므로
    # 제출과 무관한 리런에서도 실패 횟수가 늘어난다. 제출 시점에만 판정한다.
    # 비밀번호에 key 를 주지 않으므로 session_state 에는 남지 않는다.
    with st.form("gchat_login", clear_on_submit=True):
        entered = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("들어가기")

    if not submitted:
        return False

    if verify_password(entered, expected):
        st.session_state[S_AUTHED] = True
        st.session_state[S_FAILED_ATTEMPTS] = 0
        # 이 실행에서는 입력 폼이 이미 그려진 뒤다. 그대로 이어가면 폼 아래에
        # 채팅 화면이 붙어 비밀번호 칸이 남는다. 다시 그려서 폼을 지운다.
        st.rerun()

    failed += 1
    st.session_state[S_FAILED_ATTEMPTS] = failed
    if is_locked_out(failed):
        # 입력란이 남아 있으면 아직 시도할 수 있는 것처럼 보인다. 다시 그려 잠금만 보인다.
        st.rerun()
    st.error(f"비밀번호가 일치하지 않습니다 (남은 시도 {attempts_remaining(failed)}회)")
    return False
