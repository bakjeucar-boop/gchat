"""Markdown 내보내기 (계획서 2.5절).

서버 파일시스템에 쓰지 않는다. 문자열을 만들어 st.download_button 에 넘길 뿐이라
Streamlit Cloud 의 휘발성 파일시스템과 무관하다.

주의: 본문에 코드 블록이 들어 있어도 깨지지 않아야 한다. 메시지를 펜스로
감싸지 않고 그대로 이어붙이되, 인용이 필요한 곳에서만 더 긴 펜스를 쓴다.
"""

from __future__ import annotations

import re
from datetime import datetime

from gchat.models import get_model, thinking_label
from gchat.state import KST, Conversation, Message, now_kst

FILENAME_TITLE_LIMIT = 30
# 파일명에 쓸 수 없는 문자 (Windows 기준이 가장 좁다)
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FENCE = re.compile(r"^\s*(`{3,})", re.MULTILINE)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or now_kst()).astimezone(KST).strftime("%Y%m%d_%H%M")


def safe_title(title: str, limit: int = FILENAME_TITLE_LIMIT) -> str:
    """파일명에 쓸 수 있게 다듬는다 (계획서 2.5절)."""
    cleaned = _UNSAFE.sub("", " ".join(title.split())).strip(" .")
    cleaned = cleaned.replace(" ", "_")
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip("_")
    return cleaned or "대화"


def conversation_filename(conv: Conversation, moment: datetime | None = None) -> str:
    return f"gchat_{_stamp(moment)}_{safe_title(conv.title)}.md"


def archive_filename(moment: datetime | None = None) -> str:
    return f"gchat_전체_{_stamp(moment)}.md"


def fence_for(text: str) -> str:
    """본문 안의 가장 긴 백틱 울타리보다 하나 더 긴 울타리를 돌려준다.

    계획서 2.5절 — 코드 블록이 포함된 응답도 깨지지 않아야 한다.
    """
    longest = max((len(found) for found in _FENCE.findall(text)), default=0)
    return "`" * max(3, longest + 1)


def quote_block(text: str) -> str:
    """인용 블록. 빈 줄도 '>' 로 이어 블록이 끊기지 않게 한다."""
    lines = " ".join(text.split()).split("\n") if not text.strip() else text.splitlines()
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def _message_heading(message: Message) -> str:
    when = message.created_at.astimezone(KST).strftime("%H:%M")
    if message.role == "user":
        return f"## 사용자 · {when}"

    label = get_model(message.model_id).label if message.model_id else "모델"
    bits = [label, when]
    if message.in_tokens is not None:
        bits.append(f"입력 {message.in_tokens:,} / 출력 {message.out_tokens or 0:,} 토큰")
    if message.latency_s is not None:
        bits.append(f"{message.latency_s:.1f}초")
    return "## " + " · ".join(bits)


def render_conversation(conv: Conversation, *, moment: datetime | None = None) -> str:
    """대화 하나를 Markdown 으로. frontmatter + 시스템 인스트럭션 + 본문."""
    spec = get_model(conv.model_id)
    in_total = sum(m.in_tokens or 0 for m in conv.messages)
    out_total = sum(m.out_tokens or 0 for m in conv.messages)
    created = (moment or now_kst()).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    level = conv.settings.thinking_level

    lines = [
        "---",
        f"생성: {created} (KST)",
        f"제목: {conv.title}",
        f"모델: {spec.label}",
        f"응답 모드: {thinking_label(spec.id, level)} (thinking_level={level})",
        f"컨텍스트 예산: {conv.settings.context_budget:,}",
        f"메시지 수: {len(conv.messages)}",
        f"누적 토큰: 입력 {in_total:,} / 출력 {out_total:,}",
        "---",
        "",
    ]

    if conv.settings.system_instruction.strip():
        lines += ["**시스템 인스트럭션**", "", quote_block(conv.settings.system_instruction), ""]

    lines += [f"# {conv.title}", ""]

    # 절단 지점 표시 — 컨텍스트에서 빠진 마지막 메시지 다음이 경계다 (계획서 2.5절).
    trimmed = sum(1 for m in conv.messages if m.truncated_from_context)
    boundary = trimmed - 1 if trimmed else None

    for index, message in enumerate(conv.messages):
        lines.append(_message_heading(message))
        lines.append("")
        lines.append(message.content.rstrip())
        lines.append("")
        if message.truncated_output:
            lines += ["> 출력 한도로 잘린 답변입니다.", ""]
        if boundary is not None and index == boundary:
            lines += [f"> 이 시점 이전 {trimmed}개 메시지는 컨텍스트에서 제외되었습니다", ""]

    return "\n".join(lines).rstrip() + "\n"


def render_archive(conversations: list[Conversation], *, moment: datetime | None = None) -> str:
    """모든 대화를 --- 구분선으로 이어붙인다 (계획서 2.5절)."""
    stamp = (moment or now_kst()).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    head = [
        "---",
        f"생성: {stamp} (KST)",
        f"대화 수: {len(conversations)}",
        f"전체 메시지 수: {sum(len(c.messages) for c in conversations)}",
        "---",
        "",
        "# gchat 전체 대화",
        "",
    ]
    bodies = [render_conversation(conv, moment=moment) for conv in conversations]
    return "\n".join(head) + "\n\n---\n\n".join(bodies)


def has_content(conv: Conversation) -> bool:
    """대화가 비어 있으면 버튼을 비활성화한다 (계획서 2.5절)."""
    return bool(conv.messages)
