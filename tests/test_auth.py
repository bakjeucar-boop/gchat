"""비밀번호 검증 테스트 (계획서 2.7절).

streamlit 위젯을 거치지 않는 순수 로직만 검증한다. 화면 동작은 수동 확인 항목이다.
"""

from __future__ import annotations

import inspect

import pytest

from gchat import auth
from gchat.auth import MAX_ATTEMPTS, attempts_remaining, is_locked_out, verify_password

PASSWORD = "correct-horse-battery-staple-42"


def test_정확한_비밀번호는_통과한다():
    assert verify_password(PASSWORD, PASSWORD) is True


@pytest.mark.parametrize(
    "entered",
    [
        "correct-horse-battery-staple-4",  # 한 글자 짧음
        "correct-horse-battery-staple-42 ",  # 뒤 공백
        "Correct-Horse-Battery-Staple-42",  # 대소문자 다름
        "wrong",
        PASSWORD * 2,
    ],
)
def test_틀린_비밀번호는_거절한다(entered: str):
    assert verify_password(entered, PASSWORD) is False


def test_빈_입력은_거절한다():
    assert verify_password("", PASSWORD) is False


def test_설정된_비밀번호가_비어_있으면_무엇으로도_통과할_수_없다():
    assert verify_password("", "") is False
    assert verify_password("아무거나", "") is False


def test_한글_비밀번호도_비교할_수_있다():
    """hmac.compare_digest 는 비 ASCII str 을 거부하므로 바이트 비교여야 한다."""
    assert verify_password("비밀번호-한글-테스트", "비밀번호-한글-테스트") is True
    assert verify_password("비밀번호-한글-테스트", "비밀번호-한글-테스틑") is False


def test_상수_시간_비교를_쓴다():
    """== 로 비교하면 타이밍 공격에 노출된다 (계획서 2.7절)."""
    source = inspect.getsource(verify_password)
    assert "compare_digest" in source


def test_실패_5회에_잠긴다():
    assert MAX_ATTEMPTS == 5
    for failed in range(MAX_ATTEMPTS):
        assert is_locked_out(failed) is False
    assert is_locked_out(MAX_ATTEMPTS) is True
    assert is_locked_out(MAX_ATTEMPTS + 1) is True


def test_남은_시도_횟수():
    assert attempts_remaining(0) == 5
    assert attempts_remaining(4) == 1
    assert attempts_remaining(5) == 0
    assert attempts_remaining(9) == 0


def test_비밀번호를_session_state에_저장하지_않는다():
    """인증 플래그와 실패 횟수만 남긴다 (계획서 2.7절)."""
    source = inspect.getsource(auth.check_password)
    # text_input 에 key 를 주면 session_state 에 비밀번호가 남는다.
    assert "key=" not in source
