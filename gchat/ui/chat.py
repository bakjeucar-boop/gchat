"""채팅 화면 — 메시지 렌더링, 입력, 배선 (계획서 2.1 / 2.2 / 2.3 / 2.8절).

배선 순서는 계획서대로다.
    context.fit_to_budget → quota.precheck → client.stream → quota.record_usage_from

판정이 OK 가 아니면 전송하지 않는다. 사용자 입력은 대화에 남으므로 잃지 않고,
"다시 시도" 로 같은 입력을 재전송한다 (계획서 2.8절).
"""

from __future__ import annotations

import time

import streamlit as st

from gchat import state
from gchat.client import (
    EmptyResponse,
    GchatApiError,
    GeminiClient,
    InvalidApiKey,
    RateLimited,
    RequestRejected,
    ResponseBlocked,
    ServiceUnavailable,
    StreamResult,
)
from gchat.context import fit_to_budget, single_input_too_large, too_large_message
from gchat.models import default_model, thinking_label
from gchat.quota import QuotaBook, Verdict, VerdictKind
from gchat.state import Conversation, Message

S_AUTOSEND = "chat_autosend"


def render(client: GeminiClient | None, book: QuotaBook) -> None:
    conv = state.ensure_conversation()
    _render_header(conv)
    _render_messages(conv)

    if client is None:
        st.chat_input("API 키가 없어 전송할 수 없습니다", disabled=True)
        return

    prompt = st.chat_input("메시지를 입력하세요")
    if prompt:
        state.clear_deleted()
        state.append_message(conv, Message(role="user", content=prompt))
        st.session_state[S_AUTOSEND] = True
        st.rerun()

    if _awaiting_reply(conv):
        _send(conv, client, book)


def _awaiting_reply(conv: Conversation) -> bool:
    """마지막이 사용자 메시지면 아직 답하지 않은 것이다."""
    return bool(conv.messages) and conv.messages[-1].role == "user"


def _render_header(conv: Conversation) -> None:
    spec = state.active_model()
    st.caption(
        f"{conv.title} · {spec.label} · "
        f"{thinking_label(spec.id, conv.settings.thinking_level)} · "
        f"컨텍스트 예산 {conv.settings.context_budget:,}"
    )


def _render_messages(conv: Conversation) -> None:
    """절단된 메시지도 화면에는 그대로 남긴다 (계획서 2.2절)."""
    shown_truncation_note = False
    for message in conv.messages:
        if message.truncated_from_context and not shown_truncation_note:
            trimmed = sum(1 for m in conv.messages if m.truncated_from_context)
            st.info(f"앞선 {trimmed}개 메시지가 컨텍스트에서 제외됨 (모델 한도)")
            shown_truncation_note = True
        avatar = "user" if message.role == "user" else "assistant"
        with st.chat_message(avatar):
            st.markdown(message.content)
            _render_message_meta(message)


def _render_message_meta(message: Message) -> None:
    if message.role != "model":
        return
    bits = []
    if message.in_tokens is not None:
        bits.append(f"입력 {message.in_tokens:,} / 출력 {message.out_tokens or 0:,} 토큰")
    if message.latency_s is not None:
        bits.append(f"{message.latency_s:.1f}초")
    if bits:
        st.caption(" · ".join(bits))
    if message.truncated_output:
        # 계획서 2.8절 — 출력 토큰 슬라이더는 UI 에 없다. "계속" 으로 이어받는다.
        st.info("출력 한도로 잘렸습니다. '계속'이라고 입력하면 이어서 답합니다.")


def _send(conv: Conversation, client: GeminiClient, book: QuotaBook) -> None:
    """마지막 사용자 메시지에 대한 답을 만든다."""
    auto = st.session_state.pop(S_AUTOSEND, False)
    model_id = state.active_model_id()
    last_user = conv.messages[-1]

    # 계획서 2.2절 — 입력 하나만으로 요청당 한도를 넘으면 절단으로도 해결되지 않는다.
    if single_input_too_large(last_user.content, model_id):
        st.warning(too_large_message(model_id, default_model().id))
        _retry_button(conv)
        return

    trim = fit_to_budget(
        conv.messages,
        conv.settings.context_budget,
        system_instruction=conv.settings.system_instruction,
        count_exact=lambda messages: client.count_tokens(model_id, messages),
    )
    if trim.truncated:
        st.info(f"앞선 {trim.trimmed}개 메시지가 컨텍스트에서 제외됨 (모델 한도)")

    tracker = book.tracker(model_id)
    verdict = tracker.precheck(trim.tokens)
    if verdict.blocked:
        _render_blocked(verdict, trim.tokens, book, model_id)
        _retry_button(conv)
        return
    if not auto:
        # 사용자가 "다시 시도" 를 누르지 않았고 새 입력도 아니면 기다린다.
        st.caption(f"이번 요청은 약 {trim.tokens:,} 토큰을 사용합니다 · 대기 없이 전송 가능")
        _retry_button(conv, label="지금 보내기")
        return

    entry = tracker.record_sent(trim.tokens)  # 창의 기준은 전송 시각이다
    started = time.monotonic()
    result = StreamResult()
    with st.chat_message("assistant"):
        try:
            st.write_stream(client.stream(model_id, trim.messages, conv.settings, result))
        except GchatApiError as exc:
            tracker.record_usage_from(result.usage, entry)
            if isinstance(exc, RateLimited):
                tracker.apply_rate_limit(exc)
            _render_error(exc, conv, book, model_id, trim.tokens)
            return

    tracker.record_usage_from(result.usage, entry)
    state.append_message(
        conv,
        Message(
            role="model",
            content=result.text,
            model_id=model_id,
            in_tokens=result.usage.input_tokens,
            out_tokens=result.usage.output_tokens,
            latency_s=time.monotonic() - started,
            truncated_output=result.truncated,
        ),
    )
    st.rerun()


def _render_blocked(verdict: Verdict, tokens: int, book: QuotaBook, model_id: str) -> None:
    """사전 판정 결과별 안내 (계획서 2.3절 표). 시각적 완성은 세션 5."""
    if verdict.kind is VerdictKind.WAIT:
        st.info(f"⏳ {verdict.reason}")
    else:
        st.warning(verdict.reason)

    if verdict.server_wait_unknown:
        st.caption("서버가 대기 시간을 알려주지 않아 자체 계산값으로 안내합니다.")

    if verdict.kind in (VerdictKind.DAILY_EXHAUSTED, VerdictKind.TOO_LARGE, VerdictKind.WAIT):
        recommendation = book.recommend(tokens, exclude=model_id)
        if recommendation.message:
            st.caption(recommendation.message)


def _render_error(
    exc: GchatApiError, conv: Conversation, book: QuotaBook, model_id: str, tokens: int
) -> None:
    """오류 6종을 구분해 보여준다 (계획서 2.8절). 스택트레이스를 흘리지 않는다."""
    if isinstance(exc, RateLimited):
        st.warning(str(exc))
        recommendation = book.recommend(tokens, exclude=model_id)
        if recommendation.message:
            st.caption(recommendation.message)
    elif isinstance(exc, ServiceUnavailable):
        st.warning(str(exc))
    elif isinstance(exc, InvalidApiKey):
        st.error(str(exc))
    elif isinstance(exc, EmptyResponse):
        # 빈 말풍선을 남기지 않는다 (계획서 2.8절, 세션 2 A-4).
        st.error(str(exc))
    elif isinstance(exc, ResponseBlocked):
        st.error(str(exc))
    elif isinstance(exc, RequestRejected):
        st.error(str(exc))
    else:
        st.error(f"요청에 실패했습니다: {exc}")
    _retry_button(conv)


def _retry_button(conv: Conversation, label: str = "다시 시도") -> None:
    """사용자 입력을 잃지 않고 재전송한다 (계획서 2.8절)."""
    if st.button(label, key=f"retry_{conv.id}_{len(conv.messages)}"):
        st.session_state[S_AUTOSEND] = True
        st.rerun()
