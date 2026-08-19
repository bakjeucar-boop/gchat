"""채팅 화면 — 메시지 렌더링, 입력, 배선 (계획서 2.1 / 2.2 / 2.3 / 2.8절).

배선 순서는 계획서대로다.
    context.fit_to_budget → quota.precheck → client.stream → quota.record_usage_from

판정이 OK 가 아니면 전송하지 않는다. 사용자 입력은 대화에 남으므로 잃지 않고,
"다시 시도" 로 같은 입력을 재전송한다 (계획서 2.8절).
"""

from __future__ import annotations

import json
import time

import streamlit as st
import streamlit.components.v1 as components

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
from gchat.models import PURPOSE_CUSTOM, default_model
from gchat.quota import QuotaBook, Verdict, VerdictKind
from gchat.state import Conversation, Message

S_AUTOSEND = "chat_autosend"
# 스트리밍 도중 받은 조각을 쌓아 둔다. 멈춤 버튼을 누르면 실행이 통째로
# 중단되므로, 여기에 남겨 두지 않으면 그때까지 받은 답이 사라진다 (세션 6).
S_PARTIAL = "chat_partial_text"


def render_history(conv: Conversation) -> None:
    """지난 메시지를 그린다. 컨트롤 바가 이 아래·입력창 위에 놓인다.

    멈춤으로 끊긴 답을 **그리기 전에** 붙인다. 입력창 쪽에서 붙이면 이 함수가
    이미 지나간 뒤라 그 답이 화면에 나오지 않는다 — 사용자가 "계속"을 쳐서
    다시 그려질 때까지 사라진 것처럼 보인다 (세션 7 피드백).
    """
    _commit_interrupted(conv)
    _render_header(conv)
    _render_messages(conv)


def render_input(client: GeminiClient | None, book: QuotaBook, conv: Conversation) -> None:
    if client is None:
        st.chat_input("API 키가 없어 보낼 수 없습니다", disabled=True)
        return

    prompt = st.chat_input("메시지를 입력하세요")
    if prompt:
        state.clear_deleted()
        state.append_message(conv, Message(role="user", content=prompt))
        st.session_state[S_AUTOSEND] = True
        st.rerun()

    if _awaiting_reply(conv):
        _send(conv, client, book)


def _commit_interrupted(conv: Conversation) -> None:
    """멈춤으로 끊긴 답을 살려 둔다 (세션 6).

    멈춤 버튼은 리런을 일으켜 실행 중인 스크립트를 통째로 중단시킨다. 그래서
    성공 경로의 append_message 가 돌지 않는다. 쌓아 둔 조각을 여기서 붙인다.
    """
    partial = st.session_state.get(S_PARTIAL, "")
    if not partial or not _awaiting_reply(conv):
        st.session_state[S_PARTIAL] = ""
        return
    state.append_message(
        conv,
        Message(
            role="model",
            content=partial,
            model_id=state.active_model_id(),
            stopped_by_user=True,
        ),
    )
    st.session_state[S_PARTIAL] = ""


def _awaiting_reply(conv: Conversation) -> bool:
    """마지막이 사용자 메시지면 아직 답하지 않은 것이다."""
    return bool(conv.messages) and conv.messages[-1].role == "user"


# 멈춤 버튼을 입력창 바로 위에 **화면 고정**한다 (세션 7 피드백).
# 스트림 앞에 그냥 두면 대화가 길 때 뷰포트 밖(실측: top 2005px / 화면 720px)에
# 있어 보이지 않는다. 답변이 자랄수록 더 밀려난다. sticky 로는 부족하고
# (스크롤이 그 지점까지 가야 붙는다) fixed 여야 항상 보인다.
_STOP_BUTTON_CSS = """
<style>
  .st-key-gchat_stop {
    position: fixed;
    bottom: 6.2rem;
    left: 50%;
    transform: translateX(-50%);
    z-index: 1000;
    width: auto !important;
  }
  .st-key-gchat_stop button { box-shadow: 0 2px 10px rgba(0,0,0,.35); }
</style>
"""


def _render_stop_button(conv: Conversation) -> None:
    """생성 중단 (세션 6 요청 — 모델이 같은 글자를 되풀이할 때 끊는다).

    스트림 **앞에** 그려야 화면에 먼저 나온다. 누르면 리런이 걸려 실행 중인
    스크립트가 중단되므로 별도 처리기가 필요 없다.
    """
    st.markdown(_STOP_BUTTON_CSS, unsafe_allow_html=True)
    with st.container(key="gchat_stop"):
        st.button(
            "⏹ 멈춤",
            key=f"stop_{conv.id}_{len(conv.messages)}",
            help="생성을 중단합니다. 그때까지 받은 답은 남습니다.",
        )


def _capture(chunks):
    """조각을 세션에 쌓으면서 흘려보낸다. 멈춤으로 끊겨도 남는다."""
    st.session_state[S_PARTIAL] = ""
    for chunk in chunks:
        st.session_state[S_PARTIAL] += chunk
        yield chunk


def _render_header(conv: Conversation) -> None:
    """커스텀 인스트럭션이 있을 때만 한 줄 요약을 보인다 (계획서 2.6.2절).

    대화 제목은 사이드바 목록에 이미 있다. 제목이 첫 질문에서 자동 생성되므로
    본문 위에 또 쓰면 같은 문장이 두 번 보인다 (세션 6 피드백).
    범용·코딩 프리셋 문구도 굳이 매번 보일 이유가 없다.
    """
    if conv.settings.purpose != PURPOSE_CUSTOM:
        return
    instruction = conv.settings.system_instruction.strip()
    if not instruction:
        return
    summary = " ".join(instruction.split())
    if len(summary) > 60:
        summary = summary[:60] + "…"
    st.caption(f"🧭 {summary}")


def _render_messages(conv: Conversation) -> None:
    """절단된 메시지도 화면에는 그대로 남긴다 (계획서 2.2절)."""
    shown_truncation_note = False
    for index, message in enumerate(conv.messages):
        if message.truncated_from_context and not shown_truncation_note:
            trimmed = sum(1 for m in conv.messages if m.truncated_from_context)
            st.info(f"길이 제한 때문에 앞선 메시지 {trimmed}개는 이번 답변에 참고되지 않습니다.")
            shown_truncation_note = True
        avatar = "user" if message.role == "user" else "assistant"
        with st.chat_message(avatar):
            st.markdown(message.content)
            _render_message_meta(message)
            _render_copy(conv, index, message)


def _render_copy(conv: Conversation, index: int, message: Message) -> None:
    """메시지 단위 복사 — 한 번 눌러 바로 복사된다 (계획서 2.4.1절).

    복사되는 것은 렌더링된 HTML 이 아니라 **원본 Markdown** 이다. 코드 블록·
    목록·표가 살아 있어야 다른 곳에 붙일 때 쓸모가 있다. 토큰 수·지연 시간
    같은 메타 정보는 넣지 않는다.

    세션 6 피드백으로 st.code 대안(펼쳐서 다시 아이콘을 누르는 2단계)에서
    navigator.clipboard 한 번 클릭으로 바꿨다. Streamlit 컴포넌트 iframe 에는
    clipboard-write 권한이 실제로 부여되어 있다 (docs/archive/api_findings.md B-9).
    """
    if not message.content.strip():
        return
    payload = json.dumps(message.content)
    components.html(
        f"""
        <style>
          body {{ margin: 0; background: transparent; }}
          button {{
            font: 400 12px/1 "Source Sans Pro", sans-serif;
            color: rgba(128,128,128,.9);
            background: transparent; border: 1px solid rgba(128,128,128,.35);
            border-radius: 6px; padding: 4px 9px; cursor: pointer;
          }}
          button:hover {{ color: #ff4b4b; border-color: #ff4b4b; }}
        </style>
        <button id="c">📋 복사</button>
        <script>
          const btn = document.getElementById("c");
          btn.onclick = async () => {{
            try {{
              await navigator.clipboard.writeText({payload});
              btn.textContent = "✅ 복사됨";
            }} catch (e) {{
              btn.textContent = "복사 실패 — " + e.name;
            }}
            setTimeout(() => {{ btn.textContent = "📋 복사"; }}, 1500);
          }};
        </script>
        """,
        height=34,
    )


def _render_message_meta(message: Message) -> None:
    if message.role != "model":
        return
    bits = []
    if message.in_tokens is not None:
        # 이 숫자도 토큰이다. "글자분"이라고 부르면 게이지와 같은 단위 오류가
        # 되므로 단위를 주장하지 않는다 (계획서 2.7.1절).
        bits.append(f"사용량 질문 {message.in_tokens:,} · 답변 {message.out_tokens or 0:,}")
    if message.latency_s is not None:
        bits.append(f"{message.latency_s:.1f}초")
    if bits:
        st.caption(" · ".join(bits))
    if message.truncated_output:
        # 계획서 2.8절 — 출력 토큰 슬라이더는 UI 에 없다. "계속" 으로 이어받는다.
        st.info("답이 길어 중간에서 끊겼습니다. '계속'이라고 입력하면 이어서 씁니다.")
    if message.stopped_by_user:
        st.info("멈춤 버튼으로 중단했습니다. '계속'이라고 입력하면 이어서 씁니다.")


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
        st.info(f"길이 제한 때문에 앞선 메시지 {trim.trimmed}개는 이번 답변에 참고되지 않습니다.")

    tracker = book.tracker(model_id)
    verdict = tracker.precheck(trim.tokens)
    if verdict.blocked:
        _render_blocked(verdict, trim.tokens, book, model_id)
        _retry_button(conv)
        return
    if not auto:
        # 사용자가 "다시 시도" 를 누르지 않았고 새 입력도 아니면 기다린다.
        # 대기 예고는 컨트롤 바가 맡고, 0일 때는 아무것도 띄우지 않는다 (계획서 2.3절).
        _retry_button(conv, label="지금 보내기")
        return

    entry = tracker.record_sent(trim.tokens)  # 창의 기준은 전송 시각이다
    started = time.monotonic()
    result = StreamResult()

    _render_stop_button(conv)

    with st.chat_message("assistant"):
        try:
            st.write_stream(_capture(client.stream(model_id, trim.messages, conv.settings, result)))
        except GchatApiError as exc:
            st.session_state[S_PARTIAL] = ""
            tracker.record_usage_from(result.usage, entry)
            if isinstance(exc, RateLimited):
                tracker.apply_rate_limit(exc)
            _render_error(exc, conv, book, model_id, trim.tokens)
            return

    st.session_state[S_PARTIAL] = ""
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
        st.caption("남은 시간을 정확히 알 수 없어 앱이 계산한 값으로 안내합니다.")

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
        # 원문은 화면 문구에서 뺐지만 완전히 감추지도 않는다 (계획서 2.8절 —
        # 무엇이 왜 실패했는지 사용자가 알 수 있어야 한다).
        with st.expander("자세한 내용"):
            st.caption(exc.message)
    else:
        st.error("요청에 실패했습니다. 잠시 후 다시 시도해 보세요.")
        with st.expander("자세한 내용"):
            st.caption(str(exc))
    _retry_button(conv)


def _retry_button(conv: Conversation, label: str = "다시 시도") -> None:
    """사용자 입력을 잃지 않고 재전송한다 (계획서 2.8절)."""
    if st.button(label, key=f"retry_{conv.id}_{len(conv.messages)}"):
        st.session_state[S_AUTOSEND] = True
        st.rerun()
