"""Gemini API 래퍼 (계획서 3절, 2.1 / 2.8절).

세션 2 실측(docs/api_findings.md)을 그대로 반영한다.

- `temperature` / `top_p` / `top_k` / `candidate_count` 는 어느 모델에도 보내지 않는다
  (계획서 1.2절). 이 파일에 그 이름이 등장해서는 안 된다.
- `thinking_config` 는 **항상** 보낸다. 생략하면 Gemma 4 는 사고가 켜진 채 동작해
  출력 한도를 사고로 다 쓰고 빈 응답을 돌려준다 (실측 A-4).
- `tools` 는 다루지 않는다. 웹 검색 그라운딩은 v1 제외다 (계획서 2.10절).
- 오류는 삼키지 않는다. 무엇이 왜 실패했는지 구분 가능한 예외로 올린다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from gchat.models import get_model, max_output_for, resolve_thinking_level
from gchat.state import Conversation, Message, Settings

# --- 예외 -------------------------------------------------------------------


class GchatApiError(Exception):
    """이 모듈이 올리는 모든 오류의 뿌리. UI 는 이것만 잡으면 된다."""


@dataclass
class RateLimited(GchatApiError):
    """429. 계획서 2.3절 — 서버가 최종 진실이다."""

    message: str
    retry_after_s: float | None = None
    quota_id: str | None = None
    quota_metric: str | None = None
    quota_value: str | None = None

    def __str__(self) -> str:
        if self.retry_after_s is not None:
            # 올림한다. 내림하면 안내한 시각에 다시 보내도 또 429 다.
            seconds = math.ceil(self.retry_after_s)
            return f"사용량 한도에 걸렸습니다. {seconds}초 후 다시 보낼 수 있습니다."
        return "사용량 한도에 걸렸습니다. 잠시 뒤 다시 보내 주세요."

    @property
    def is_daily(self) -> bool:
        """일일 한도(RPD)인가. quotaId 에 PerDay 가 들어간다."""
        return bool(self.quota_id and "PerDay" in self.quota_id)


@dataclass
class ServiceUnavailable(GchatApiError):
    """503. 일시적 과부하 — 세션 2 실측에서 실제로 발생했다. 429 와 구분한다."""

    message: str

    def __str__(self) -> str:
        return "지금 서버가 붐빕니다. 잠시 후 다시 보내 주세요."


@dataclass
class RequestRejected(GchatApiError):
    """400 등 요청 자체가 거부된 경우 (예: Gemma 에 thinking_level=medium)."""

    message: str

    def __str__(self) -> str:
        # 원문(400 INVALID_ARGUMENT + JSON)은 message 에 남겨 두고 화면에는 쓰지
        # 않는다. 화면에 필요한 사람은 "자세한 내용"을 펼쳐 본다 (세션 7 피드백).
        return "이 설정으로는 보낼 수 없습니다. 응답 모드나 용도를 바꿔 다시 시도해 보세요."


@dataclass
class InvalidApiKey(GchatApiError):
    """API 키가 잘못된 경우. 원문 JSON 을 화면에 흘리지 않는다 (계획서 5절 24번)."""

    def __str__(self) -> str:
        return "API 키가 올바르지 않습니다. 앱 설정에 등록한 키를 확인해 주세요."


@dataclass
class ResponseBlocked(GchatApiError):
    """SAFETY / RECITATION 등으로 응답이 막힌 경우 (계획서 2.8절)."""

    reason: str

    def __str__(self) -> str:
        return "안전 정책에 걸려 답하지 못했습니다. 표현을 바꿔 다시 물어보세요."


@dataclass
class EmptyResponse(GchatApiError):
    """본문이 비어 돌아온 경우.

    세션 2 실측: 사고 토큰이 max_output_tokens 를 다 쓰면 본문이 빈 채로
    MAX_TOKENS 가 온다. 조용히 빈 말풍선을 남기지 않는다.
    """

    finish_reason: str | None = None
    thoughts_tokens: int = 0

    def __str__(self) -> str:
        if self.thoughts_tokens:
            return (
                "생각만 하다가 답을 쓰지 못했습니다. 응답 모드를 한 단계 낮추면 대체로 해결됩니다."
            )
        return "빈 답이 돌아왔습니다. 질문을 조금 바꿔 다시 보내 보세요."


# --- 응답 부가 정보 -----------------------------------------------------------


@dataclass
class Usage:
    """usage_metadata. 실측상 각 필드는 None 일 수 있어 0 으로 채운다 (실측 A-6)."""

    input_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_metadata(cls, meta: Any) -> Usage:
        if meta is None:
            return cls()
        return cls(
            input_tokens=getattr(meta, "prompt_token_count", None) or 0,
            output_tokens=getattr(meta, "candidates_token_count", None) or 0,
            thoughts_tokens=getattr(meta, "thoughts_token_count", None) or 0,
            total_tokens=getattr(meta, "total_token_count", None) or 0,
        )


@dataclass
class StreamResult:
    """스트리밍이 끝난 뒤에야 알 수 있는 것들을 담는다."""

    text: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        """출력 한도로 잘렸는가 (계획서 2.8절 — "계속"으로 이어받게 안내한다)."""
        return self.finish_reason == "MAX_TOKENS"


# --- 오류 해석 ---------------------------------------------------------------

_RETRY_IN = re.compile(r"retry in ([0-9.]+)s", re.IGNORECASE)
_RETRY_DELAY = re.compile(r"['\"]retryDelay['\"]:\s*['\"](\d+(?:\.\d+)?)s['\"]")
_QUOTA_ID = re.compile(r"['\"]quotaId['\"]:\s*['\"]([^'\"]+)['\"]")
_QUOTA_METRIC = re.compile(r"['\"]quotaMetric['\"]:\s*['\"]([^'\"]+)['\"]")
_QUOTA_VALUE = re.compile(r"['\"]quotaValue['\"]:\s*['\"]([^'\"]+)['\"]")


def parse_retry_after(text: str) -> float | None:
    """429 본문에서 대기 시간을 뽑는다 (실측 B-3).

    두 곳에 서로 다른 형태로 실려 온다.
      - details 의 google.rpc.RetryInfo → "31s"
      - message 본문 → "Please retry in 31.691162057s."
    소수점이 있는 message 쪽을 먼저 본다. 둘 다 없는 429 도 실재하므로
    그때는 None 을 돌려준다 (그라운딩 429 가 그랬다).
    """
    for pattern in (_RETRY_IN, _RETRY_DELAY):
        found = pattern.search(text)
        if found:
            try:
                return float(found.group(1))
            except ValueError:
                continue
    return None


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    found = pattern.search(text)
    return found.group(1) if found else None


def translate_error(exc: Exception) -> GchatApiError:
    """SDK 예외를 이 앱의 예외로 옮긴다. 스택트레이스를 UI 로 흘리지 않는다."""
    if isinstance(exc, GchatApiError):
        return exc
    code = getattr(exc, "code", None)
    text = f"{getattr(exc, 'details', '')} {exc}"
    if code == 429:
        return RateLimited(
            message=str(exc)[:500],
            retry_after_s=parse_retry_after(text),
            quota_id=_first(_QUOTA_ID, text),
            quota_metric=_first(_QUOTA_METRIC, text),
            quota_value=_first(_QUOTA_VALUE, text),
        )
    if code == 503:
        return ServiceUnavailable(message=str(exc)[:500])
    if code == 400:
        if "API key not valid" in text or "API_KEY_INVALID" in text:
            return InvalidApiKey()
        return RequestRejected(message=str(exc)[:300])
    return GchatApiError(str(exc)[:500])


# --- 요청 조립 ---------------------------------------------------------------


def to_contents(messages: list[Message]) -> list[types.Content]:
    """대화 이력을 API 의 contents 배열로 옮긴다.

    계획서 2.1절 금지 사항: 마지막 턴을 model 역할로 끝내지 않는다.
    """
    contents = [
        types.Content(role=m.role, parts=[types.Part(text=m.content)])
        for m in messages
        if m.content
    ]
    while contents and contents[-1].role == "model":
        contents.pop()
    return contents


def build_config(model_id: str, settings: Settings) -> types.GenerateContentConfig:
    """설정을 GenerateContentConfig 로 번역한다.

    thinking_level 외에 응답 성향을 조절하는 파라미터는 보내지 않는다 (계획서 1.2절).
    """
    spec = get_model(model_id)
    level = resolve_thinking_level(model_id, settings.thinking_level)
    kwargs: dict[str, Any] = {
        # 용도별 상한 (계획서 1.4절 — 코딩일 때만 올라간다)
        "max_output_tokens": max_output_for(model_id, settings.purpose),
        # 항상 명시한다. 생략 시 Gemma 는 사고가 켜진다 (실측 A-4).
        "thinking_config": types.ThinkingConfig(thinking_level=level),
    }
    if settings.system_instruction and spec.supports_system_instruction:
        kwargs["system_instruction"] = settings.system_instruction
    return types.GenerateContentConfig(**kwargs)


# --- 클라이언트 ---------------------------------------------------------------


class GeminiClient:
    """google-genai 래퍼. 모델별 분기는 models.py 테이블만 참조한다."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise GchatApiError("API 키가 없습니다. 앱 설정에 키를 등록해 주세요.")
        self._client = genai.Client(api_key=api_key)

    def count_tokens(self, model_id: str, messages: list[Message]) -> int:
        """이력이 몇 토큰인지 센다. 실측상 세 모델의 결과는 사실상 같다 (B-5)."""
        contents = to_contents(messages)
        if not contents:
            return 0
        try:
            resp = self._client.models.count_tokens(model=model_id, contents=contents)
        except Exception as exc:  # noqa: BLE001 — 번역해서 올린다
            raise translate_error(exc) from exc
        return resp.total_tokens or 0

    def stream(
        self,
        model_id: str,
        messages: list[Message],
        settings: Settings,
        result: StreamResult,
    ) -> Iterator[str]:
        """응답을 조각으로 흘린다 (계획서 2.1절 — 항상 스트리밍).

        `result` 는 호출자가 만들어 넘기는 그릇이다. 스트림이 끝나야 알 수 있는
        토큰 수와 finish_reason 을 여기에 채운다. st.write_stream 은 조각만
        받아가므로 부가 정보를 돌려줄 다른 통로가 필요하다.
        """
        contents = to_contents(messages)
        if not contents:
            raise RequestRejected("보낼 사용자 메시지가 없습니다.")
        config = build_config(model_id, settings)

        pieces: list[str] = []
        try:
            for chunk in self._client.models.generate_content_stream(
                model=model_id, contents=contents, config=config
            ):
                if chunk.usage_metadata is not None:
                    result.usage = Usage.from_metadata(chunk.usage_metadata)
                if chunk.candidates:
                    reason = chunk.candidates[0].finish_reason
                    if reason is not None:
                        result.finish_reason = getattr(reason, "name", str(reason))
                if chunk.text:
                    pieces.append(chunk.text)
                    yield chunk.text
        except Exception as exc:  # noqa: BLE001 — 번역해서 올린다
            raise translate_error(exc) from exc

        result.text = "".join(pieces)
        if result.finish_reason in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
            raise ResponseBlocked(reason=result.finish_reason)
        if not result.text:
            raise EmptyResponse(
                finish_reason=result.finish_reason,
                thoughts_tokens=result.usage.thoughts_tokens,
            )

    def stream_conversation(
        self, conversation: Conversation, model_id: str, result: StreamResult
    ) -> Iterator[str]:
        """대화 하나를 그대로 흘려보낸다. 컨텍스트 절단은 세션 3의 context.py 몫이다."""
        return self.stream(model_id, conversation.messages, conversation.settings, result)
