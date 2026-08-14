"""대화 자료구조와 session_state 헬퍼 (계획서 2.4절).

대화는 st.session_state 에만 존재한다. 저장소도 DB 도 두지 않는다.
새로고침하면 사라지는 것이 v1 의 설계 결과다.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import streamlit as st

from gchat.models import (
    PURPOSE_CUSTOM,
    PURPOSE_GENERAL,
    ModelSpec,
    compose_instruction,
    default_model,
    get_model,
    resolve_thinking_level,
    thinking_label,
)

KST = timezone(timedelta(hours=9))

# session_state 키. 문자열을 여기저기 흩어놓지 않는다.
S_AUTHED = "authed"
S_CONVERSATIONS = "conversations"
S_ACTIVE_CONVERSATION = "active_conversation_id"
S_ACTIVE_MODEL = "active_model_id"  # 확정된 모델. 실제 API 호출에 쓰는 값
S_UI_MODEL = "ui_model_id"  # selectbox 위젯 값. 확인 절차 중에는 확정값과 다를 수 있다
S_PENDING_MODEL = "pending_model_id"  # 계열 전환 확인 대기 중인 대상 모델
S_LAST_DELETED = "last_deleted_conversation"  # 되돌리기 버퍼 (계획서 2.4절)


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
    # 계획서 2.4절 자료구조에는 없으나 2.8절(응답 지연 시간 표시)과
    # 2.5절(내보내기 "· 3.2초")이 요구하므로 세션 4에서 추가했다.
    latency_s: float | None = None
    # 출력 한도로 잘렸는가 (계획서 2.8절 — "계속" 안내를 붙인다)
    truncated_output: bool = False
    # 사용자가 멈춤 버튼으로 끊었는가 (세션 6 추가 — 계획서 2.4절 구조에는 없다)
    stopped_by_user: bool = False


@dataclass
class Settings:
    """대화별 설정 (계획서 2.6 / 2.6.1 / 2.6.2절).

    앱 전역 설정이 아니라 대화의 속성이다. 별도 설정 메뉴는 두지 않는다.
    웹 검색 토글은 없다 — 계획서 2.10절이 세션 2 실측으로 v1에서 제외됐다.
    """

    thinking_level: str
    context_budget: int
    system_instruction: str = ""
    # 용도 프리셋 (세션 6). 범용·코딩은 문구가 자동으로 채워지고 화면에 숨긴다.
    # 커스텀일 때만 사용자가 직접 쓰고 입력칸이 보인다.
    purpose: str = PURPOSE_GENERAL

    @property
    def system_instruction_customized(self) -> bool:
        """사용자가 직접 쓴 인스트럭션인가 (계획서 2.6.2절의 플래그를 대체)."""
        return self.purpose == PURPOSE_CUSTOM

    @classmethod
    def for_model(cls, spec: ModelSpec, *, inherit: Settings | None = None) -> Settings:
        """모델 기본값으로 설정을 만든다.

        계획서 2.1.1절: 새 대화는 직전 대화의 응답 모드를 이어받되, 컨텍스트
        예산은 새 모델의 기본값으로 초기화한다. 응답 모드가 새 모델에 없는
        값이면 minimal 로 되돌린다.
        용도(2.6.2절)도 이어받는다. 커스텀이면 사용자가 쓴 문구를 그대로 두고,
        아니면 새 모델에 맞춰 다시 조합한다 — 모델별 길이 지시가 달라지기 때문이다.
        """
        level = inherit.thinking_level if inherit else spec.default_thinking_level
        purpose = inherit.purpose if inherit else PURPOSE_GENERAL
        instruction = (
            inherit.system_instruction
            if inherit and purpose == PURPOSE_CUSTOM
            else compose_instruction(spec.id, purpose)
        )
        return cls(
            thinking_level=resolve_thinking_level(spec.id, level),
            context_budget=spec.default_context_budget,
            system_instruction=instruction,
            purpose=purpose,
        )


def adopt_model(settings: Settings, model_id: str) -> list[str]:
    """계열 내 모델 전환에 맞춰 설정을 손본다 (계획서 2.6.1절 동작 규칙).

    이력은 유지하므로 예산은 건드리지 않는다. 바뀐 사실을 알릴 문구를 돌려준다.
    """
    spec = get_model(model_id)
    notes: list[str] = []

    resolved = resolve_thinking_level(model_id, settings.thinking_level)
    if resolved != settings.thinking_level:
        notes.append(
            f"'{settings.thinking_level}' 응답 모드는 {spec.label}에 없어 "
            f"'{thinking_label(model_id, resolved)}'로 되돌렸습니다."
        )
        settings.thinking_level = resolved

    if not settings.system_instruction_customized:
        composed = compose_instruction(model_id, settings.purpose)
        if settings.system_instruction != composed:
            settings.system_instruction = composed
            notes.append(f"시스템 인스트럭션을 {spec.label}에 맞게 다시 맞췄습니다.")
    return notes


def set_purpose(settings: Settings, model_id: str, purpose: str) -> None:
    """용도를 바꾼다 (세션 6).

    범용·코딩은 문구를 다시 조합한다. 커스텀으로 넘어갈 때는 지금 문구를
    그대로 남겨 사용자가 이어서 고칠 수 있게 한다 — 빈 칸을 주면 처음부터
    써야 해서 불친절하다.
    """
    settings.purpose = purpose
    if purpose != PURPOSE_CUSTOM:
        settings.system_instruction = compose_instruction(model_id, purpose)


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
    # 계획서 2.4절 자료구조에는 없으나, "사용자가 지정한 제목은 자르지 않는다"를
    # 지키려면 자동 제목과 수동 제목을 구분해야 한다 (세션 6 추가).
    title_customized: bool = False

    def touch(self) -> None:
        self.updated_at = now_kst()

    @property
    def is_empty(self) -> bool:
        """메시지가 하나도 없는 대화인가 (계획서 2.4절 — 빈 대화는 최대 1개)."""
        return not self.messages

    def display_title(self) -> str:
        """목록에 보일 제목. 수동 지정한 제목은 자르지 않는다 (계획서 2.4절)."""
        if self.title_customized:
            return self.title
        return shorten_title(self.title)


# 계획서 2.4절 — 목록에서 읽기 쉽게 20자로 줄인다 (세션 5 실사용 후 30 → 20).
TITLE_LIMIT = 20
DEFAULT_TITLE = "새 대화"


def title_from_first_message(text: str) -> str:
    """첫 사용자 메시지에서 제목을 만든다 (계획서 2.4절).

    자르지 않고 한 줄로만 눌러 보관한다. 목록에서 20자로 줄이는 것은
    표시 단계(shorten_title)의 몫이다 — 툴팁에 전체를 보여줘야 하므로
    원본을 잃으면 안 된다.
    """
    collapsed = " ".join(text.split())
    return collapsed or DEFAULT_TITLE


def shorten_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """목록 표시용으로 줄인다. 넘치면 뒤에 말줄임표를 붙인다 (계획서 2.4절)."""
    collapsed = " ".join(title.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def sort_by_recent(items: list[Conversation]) -> list[Conversation]:
    """사이드바는 최신순으로 보여준다 (계획서 2.4절)."""
    return sorted(items, key=lambda conv: conv.updated_at, reverse=True)


def remove_conversation(items: list[Conversation], conv_id: str) -> Conversation | None:
    """목록에서 빼내 돌려준다. 없으면 None. (되돌리기용으로 보관한다)"""
    for index, conv in enumerate(items):
        if conv.id == conv_id:
            return items.pop(index)
    return None


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
    if S_LAST_DELETED not in st.session_state:
        st.session_state[S_LAST_DELETED] = None


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


def ensure_conversation() -> Conversation:
    """활성 대화를 보장한다. 없으면 현재 모델로 하나 만든다."""
    conv = active_conversation()
    if conv is None:
        conv = add_conversation(new_conversation(active_model_id()))
    return conv


def start_conversation(model_id: str, *, inherit_from: Conversation | None = None) -> Conversation:
    """새 대화를 시작한다. 직전 대화의 설정을 이어받는다 (계획서 2.1.1절).

    **빈 대화는 만들지 않는다** (계획서 2.4절, 세션 5 실사용 후 추가).
    지금 대화가 이미 비어 있으면 새로 만들지 않고 그 대화의 모델만 바꾼다.
    빈 대화는 언제나 최대 1개다.
    """
    current = active_conversation()
    if current is not None and current.is_empty:
        switch_model_in_place(current, model_id)
        return current
    return add_conversation(
        new_conversation(model_id, inherit=inherit_from.settings if inherit_from else None)
    )


def switch_model_in_place(conv: Conversation, model_id: str) -> list[str]:
    """대화를 유지한 채 모델만 바꾼다.

    빈 대화의 계열 전환(계획서 2.4절)과 계열 내 전환(2.6.1.1절)에 쓴다.
    계열이 달라지면 예산도 새 모델 기본값으로 맞춘다 — 이력이 없으니 절단 걱정이 없다.
    """
    spec = get_model(model_id)
    changed_family = get_model(conv.model_id).family != spec.family
    conv.model_id = spec.id
    notes = adopt_model(conv.settings, spec.id)
    if changed_family:
        conv.settings.context_budget = spec.default_context_budget
    return notes


def append_message(conv: Conversation, message: Message) -> None:
    """메시지를 붙이고 제목·수정 시각을 갱신한다."""
    conv.messages.append(message)
    if message.role == "user" and conv.title == DEFAULT_TITLE and not conv.title_customized:
        conv.title = title_from_first_message(message.content)
    conv.touch()


def rename_conversation(conv: Conversation, title: str) -> None:
    """사용자가 지정한 제목. 이후로는 자르지 않는다 (계획서 2.4절)."""
    cleaned = " ".join(title.split())
    if cleaned:
        conv.title = cleaned
        conv.title_customized = True
    else:
        conv.title = DEFAULT_TITLE
        conv.title_customized = False


def recent_conversations() -> list[Conversation]:
    return sort_by_recent(conversations())


def delete_conversation(conv_id: str) -> Conversation | None:
    """대화를 지우고 되돌리기 버퍼에 넣는다 (계획서 2.4절).

    계획서는 "되돌리기 5초"지만 Streamlit 은 상호작용이 있어야 화면을 다시
    그리므로, 세션 4 결정에 따라 **다음 조작까지** 되돌리기 버튼을 띄운다.
    """
    removed = remove_conversation(conversations(), conv_id)
    if removed is None:
        return None
    st.session_state[S_LAST_DELETED] = removed
    if st.session_state.get(S_ACTIVE_CONVERSATION) == conv_id:
        remaining = recent_conversations()
        st.session_state[S_ACTIVE_CONVERSATION] = remaining[0].id if remaining else None
    return removed


def restore_deleted() -> Conversation | None:
    """되돌리기. 지운 대화를 목록에 되돌리고 활성화한다."""
    removed = st.session_state.get(S_LAST_DELETED)
    if removed is None:
        return None
    conversations().append(removed)
    st.session_state[S_ACTIVE_CONVERSATION] = removed.id
    st.session_state[S_LAST_DELETED] = None
    return removed


def deleted_conversation() -> Conversation | None:
    return st.session_state.get(S_LAST_DELETED)


def clear_deleted() -> None:
    """다른 조작이 일어나면 되돌리기 기회를 거둔다 (세션 4 결정)."""
    st.session_state[S_LAST_DELETED] = None


def delete_all_conversations() -> int:
    """일괄 삭제. 되돌리기를 제공하지 않는다 (계획서 2.4절)."""
    count = len(conversations())
    st.session_state[S_CONVERSATIONS] = []
    st.session_state[S_ACTIVE_CONVERSATION] = None
    st.session_state[S_LAST_DELETED] = None
    return count


def needs_family_confirmation(from_model_id: str, to_model_id: str) -> bool:
    """계열이 바뀌는 전환인가 (계획서 2.1.1절).

    계열 내 전환(31B ↔ 26B)은 한도·예산이 같아 절단이 없으므로 확인하지 않는다.
    """
    return get_model(from_model_id).family != get_model(to_model_id).family
