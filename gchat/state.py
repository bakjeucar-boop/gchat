"""대화 자료구조와 session_state 헬퍼 (계획서 2.4절).

대화는 st.session_state 에만 존재한다. 저장소도 DB 도 두지 않는다.
새로고침하면 사라지는 것이 v1 의 설계 결과다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import streamlit as st

from gchat.models import ModelSpec, default_model, get_model, resolve_thinking_level

KST = timezone(timedelta(hours=9))

# session_state 키. 문자열을 여기저기 흩어놓지 않는다.
S_AUTHED = "authed"
S_CONVERSATIONS = "conversations"
S_ACTIVE_CONVERSATION = "active_conversation_id"
S_ACTIVE_MODEL = "active_model_id"  # 확정된 모델. 실제 API 호출에 쓰는 값
S_UI_MODEL = "ui_model_id"  # selectbox 위젯 값. 확인 절차 중에는 확정값과 다를 수 있다
S_PENDING_MODEL = "pending_model_id"  # 계열 전환 확인 대기 중인 대상 모델


def now_kst() -> datetime:
    return datetime.now(KST)


@dataclass
class Message:
    """대화의 한 턴 (계획서 2.4절)."""

    role: str  # "user" | "model"
    content: str
    model_id: str | None = None
    in_tokens: int | None = None
    out_tokens: int | None = None
    created_at: datetime = field(default_factory=now_kst)
    truncated_from_context: bool = False


@dataclass
class Settings:
    """대화별 설정 (계획서 2.6 / 2.6.1 / 2.6.2 / 2.10절).

    앱 전역 설정이 아니라 대화의 속성이다. 별도 설정 메뉴는 두지 않는다.
    """

    thinking_level: str
    context_budget: int
    system_instruction: str = ""
    web_search: bool = False  # 계획서 2.10절 — 새 대화마다 꺼짐으로 초기화

    @classmethod
    def for_model(cls, spec: ModelSpec, *, inherit: Settings | None = None) -> Settings:
        """모델 기본값으로 설정을 만든다.

        계획서 2.1.1절: 새 대화는 직전 대화의 응답 모드와 시스템 인스트럭션을
        이어받되, 컨텍스트 예산은 새 모델의 기본값으로 초기화한다. 응답 모드가
        새 모델에 없는 값이면 minimal 로 되돌린다.
        웹 검색은 이어받지 않고 항상 꺼짐으로 시작한다 (계획서 2.10절).
        """
        level = inherit.thinking_level if inherit else spec.default_thinking_level
        return cls(
            thinking_level=resolve_thinking_level(spec.id, level),
            context_budget=spec.default_context_budget,
            system_instruction=inherit.system_instruction if inherit else "",
            web_search=False,
        )


@dataclass
class Conversation:
    """대화 하나 (계획서 2.4절)."""

    id: str
    title: str
    messages: list[Message]
    model_id: str
    settings: Settings
    created_at: datetime = field(default_factory=now_kst)
    updated_at: datetime = field(default_factory=now_kst)

    def touch(self) -> None:
        self.updated_at = now_kst()


def new_conversation(model_id: str, *, inherit: Settings | None = None) -> Conversation:
    spec = get_model(model_id)
    return Conversation(
        id=uuid.uuid4().hex[:8],
        title="새 대화",
        messages=[],
        model_id=spec.id,
        settings=Settings.for_model(spec, inherit=inherit),
    )


# --- session_state 접근 -------------------------------------------------------


def init_session_state() -> None:
    """앱 진입 시 한 번 호출한다. 이미 있는 값은 건드리지 않는다."""
    if S_ACTIVE_MODEL not in st.session_state:
        st.session_state[S_ACTIVE_MODEL] = default_model().id
    if S_UI_MODEL not in st.session_state:
        st.session_state[S_UI_MODEL] = st.session_state[S_ACTIVE_MODEL]
    if S_PENDING_MODEL not in st.session_state:
        st.session_state[S_PENDING_MODEL] = None
    if S_CONVERSATIONS not in st.session_state:
        st.session_state[S_CONVERSATIONS] = []
    if S_ACTIVE_CONVERSATION not in st.session_state:
        st.session_state[S_ACTIVE_CONVERSATION] = None


def conversations() -> list[Conversation]:
    return st.session_state[S_CONVERSATIONS]


def active_conversation() -> Conversation | None:
    target = st.session_state.get(S_ACTIVE_CONVERSATION)
    if target is None:
        return None
    for conv in conversations():
        if conv.id == target:
            return conv
    return None


def add_conversation(conv: Conversation) -> Conversation:
    """새 대화를 목록 맨 앞에 넣고 활성화한다 (사이드바는 최신순)."""
    conversations().insert(0, conv)
    st.session_state[S_ACTIVE_CONVERSATION] = conv.id
    return conv


def active_model_id() -> str:
    """확정된 모델 ID. API 호출과 한도 판정은 항상 이 값을 쓴다."""
    return st.session_state[S_ACTIVE_MODEL]


def active_model() -> ModelSpec:
    return get_model(active_model_id())


def selected_model_id() -> str:
    """selectbox 가 들고 있는 값. 확인 절차 중에는 확정값과 다르다."""
    return st.session_state[S_UI_MODEL]


def commit_model_selection(model_id: str) -> None:
    """모델 전환을 확정한다. UI 선택값도 함께 맞춘다."""
    spec = get_model(model_id)
    st.session_state[S_ACTIVE_MODEL] = spec.id
    st.session_state[S_UI_MODEL] = spec.id
    st.session_state[S_PENDING_MODEL] = None


def revert_model_selection() -> None:
    """계열 전환 확인을 취소했을 때 드롭다운을 확정 모델로 되돌린다.

    계획서 2.1.1절 구현 주의사항. st.selectbox 는 선택 즉시 값이 바뀌므로
    취소 시 위젯 값을 되돌려 주지 않으면 화면과 실제 모델이 어긋난다.
    """
    st.session_state[S_UI_MODEL] = st.session_state[S_ACTIVE_MODEL]
    st.session_state[S_PENDING_MODEL] = None


def set_pending_model(model_id: str) -> None:
    """계열 전환 확인 단계로 들어간다 (확정하지 않는다)."""
    st.session_state[S_PENDING_MODEL] = get_model(model_id).id


def pending_model_id() -> str | None:
    return st.session_state.get(S_PENDING_MODEL)


def needs_family_confirmation(from_model_id: str, to_model_id: str) -> bool:
    """계열이 바뀌는 전환인가 (계획서 2.1.1절).

    계열 내 전환(31B ↔ 26B)은 한도·예산이 같아 절단이 없으므로 확인하지 않는다.
    """
    return get_model(from_model_id).family != get_model(to_model_id).family
