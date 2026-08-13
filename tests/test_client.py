"""client.py 테스트 — API 를 호출하지 않는 순수 로직만 검증한다.

실제 호출 결과는 docs/api_findings.md 에 있고, 여기서는 그 실측을 코드가
제대로 반영하는지만 본다.
"""

from __future__ import annotations

import inspect

import pytest
from google.genai import types

from gchat import client as client_mod
from gchat.client import (
    EmptyResponse,
    InvalidApiKey,
    RateLimited,
    RequestRejected,
    ServiceUnavailable,
    StreamResult,
    Usage,
    build_config,
    parse_retry_after,
    to_contents,
    translate_error,
)
from gchat.models import get_model
from gchat.state import Message, Settings

# 세션 2 실측에서 실제로 받은 429 본문 (docs/api_findings.md B-3)
REAL_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current "
    "quota, please check your plan and billing details. ... \\n* Quota exceeded for metric: "
    "generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 15, "
    "model: gemini-3.5-flash-lite\\nPlease retry in 31.691162057s.', 'status': "
    "'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', "
    "'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/"
    "generate_content_free_tier_requests', 'quotaId': "
    "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '15'}]}, "
    "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '31s'}]}}"
)

# 그라운딩 429 — RetryInfo 도 QuotaFailure 도 없다 (실측 C-2)
REAL_429_NO_RETRY = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current "
    "quota, please check your plan and billing details.', 'status': 'RESOURCE_EXHAUSTED', "
    "'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': []}]}}"
)


def level_of(cfg: types.GenerateContentConfig) -> str:
    """SDK 가 thinking_level 을 ThinkingLevel.MINIMAL 같은 열거형으로 정규화한다."""
    raw = cfg.thinking_config.thinking_level
    return str(getattr(raw, "value", raw)).lower()


class FakeApiError(Exception):
    """SDK 의 ClientError 를 흉내낸다 (code / details 속성)."""

    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.details = text


# --- 금지 파라미터 -------------------------------------------------------------


def test_샘플링_파라미터를_어디에서도_보내지_않는다():
    """계획서 1.2절 — 이 파일에 그 이름이 등장해서는 안 된다."""
    source = inspect.getsource(client_mod)
    body = source.split('"""', 2)[-1]  # 모듈 docstring 은 금지 사실을 설명하므로 제외
    for name in ("temperature", "top_p", "top_k", "candidate_count"):
        assert f"{name}=" not in body


def test_검색_도구를_다루지_않는다():
    """계획서 2.10절 v1 제외 — tools 파라미터를 쓰지 않는다."""
    source = inspect.getsource(client_mod)
    assert "GoogleSearch" not in source
    assert "tools=" not in source


# --- config 번역 --------------------------------------------------------------


def test_thinking_config를_항상_보낸다():
    """실측 A-4 — 생략하면 Gemma 는 사고가 켜진 채 동작한다."""
    for model_id in ("gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it"):
        cfg = build_config(model_id, Settings(thinking_level="minimal", context_budget=3_000))
        assert cfg.thinking_config is not None
        assert level_of(cfg) == "minimal"


def test_지원하지_않는_사고_수준은_보내기_전에_걸러진다():
    """실측 A-5 — Gemma 에 medium 을 보내면 400 이다."""
    cfg = build_config("gemma-4-31b-it", Settings(thinking_level="medium", context_budget=3_000))
    assert level_of(cfg) == "minimal"
    # Gemini 는 medium 을 그대로 쓴다
    cfg = build_config(
        "gemini-3.5-flash-lite", Settings(thinking_level="medium", context_budget=32_000)
    )
    assert level_of(cfg) == "medium"


def test_최대_출력은_모델_테이블에서_가져온다():
    """UI 에 노출하지 않는 내부 상수다 (계획서 2.6절)."""
    for model_id in ("gemini-3.5-flash-lite", "gemma-4-31b-it"):
        cfg = build_config(model_id, Settings(thinking_level="minimal", context_budget=3_000))
        assert cfg.max_output_tokens == get_model(model_id).default_max_output


def test_시스템_인스트럭션은_있을_때만_보낸다():
    empty = build_config(
        "gemini-3.5-flash-lite", Settings(thinking_level="minimal", context_budget=32_000)
    )
    assert empty.system_instruction is None

    filled = build_config(
        "gemini-3.5-flash-lite",
        Settings(thinking_level="minimal", context_budget=32_000, system_instruction="3문장 이내"),
    )
    assert filled.system_instruction == "3문장 이내"


def test_config에_금지_파라미터가_비어_있다():
    cfg = build_config(
        "gemini-3.5-flash-lite", Settings(thinking_level="minimal", context_budget=32_000)
    )
    assert cfg.temperature is None
    assert cfg.top_p is None
    assert cfg.top_k is None
    assert cfg.candidate_count is None


# --- contents 조립 -------------------------------------------------------------


def test_이력을_contents로_옮긴다():
    messages = [
        Message(role="user", content="안녕"),
        Message(role="model", content="네"),
        Message(role="user", content="1+1은?"),
    ]
    contents = to_contents(messages)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "안녕"


def test_마지막_턴이_model이면_잘라낸다():
    """계획서 2.1절 금지 — model 역할로 끝내면 Gemini 3.x 가 400 을 낸다."""
    messages = [
        Message(role="user", content="안녕"),
        Message(role="model", content="네"),
    ]
    contents = to_contents(messages)
    assert [c.role for c in contents] == ["user"]


def test_빈_메시지는_보내지_않는다():
    messages = [Message(role="user", content=""), Message(role="user", content="질문")]
    assert len(to_contents(messages)) == 1


def test_사용자_메시지가_없으면_거부한다():
    from gchat.client import GeminiClient

    cli = GeminiClient.__new__(GeminiClient)  # 네트워크 없이 메서드만 쓴다
    with pytest.raises(RequestRejected):
        list(
            cli.stream(
                "gemini-3.5-flash-lite",
                [Message(role="model", content="답")],
                Settings(thinking_level="minimal", context_budget=32_000),
                StreamResult(),
            )
        )


# --- 오류 번역 ------------------------------------------------------------------


def test_실제_429에서_대기시간과_quota를_뽑는다():
    err = translate_error(FakeApiError(429, REAL_429))
    assert isinstance(err, RateLimited)
    assert err.retry_after_s == pytest.approx(31.691162057)
    assert err.quota_id == "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    assert err.quota_value == "15"
    assert err.is_daily is False
    # 안내는 올림한다. 31.69초를 31초로 안내하면 그때 다시 보내도 또 429 다.
    assert "32초 후" in str(err)


def test_대기시간이_없는_429도_처리한다():
    """실측 C-2 — 그라운딩 429 에는 RetryInfo 가 없다."""
    err = translate_error(FakeApiError(429, REAL_429_NO_RETRY))
    assert isinstance(err, RateLimited)
    assert err.retry_after_s is None
    assert "알려주지 않았습니다" in str(err)


def test_일일_한도는_quotaId로_구분한다():
    text = "{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'} retry in 60s"
    err = translate_error(FakeApiError(429, text))
    assert isinstance(err, RateLimited)
    assert err.is_daily is True


def test_retryDelay_형식도_읽는다():
    assert parse_retry_after("'retryDelay': '31s'") == 31.0
    assert parse_retry_after("Please retry in 19.917078239s.") == pytest.approx(19.917078239)
    assert parse_retry_after("아무 정보 없음") is None


def test_503은_429와_구분한다():
    """실측 B-1 — 병렬 요청 중 실제로 발생했다."""
    err = translate_error(FakeApiError(503, "503 UNAVAILABLE. high demand"))
    assert isinstance(err, ServiceUnavailable)
    assert "혼잡" in str(err)


def test_400은_요청_거부로_옮긴다():
    err = translate_error(FakeApiError(400, "Thinking level is not supported for this model."))
    assert isinstance(err, RequestRejected)
    assert "거부" in str(err)


def test_잘못된_API_키는_원문을_노출하지_않는다():
    """계획서 5절 24번 — 명확한 오류, 스택트레이스·원문 JSON 금지."""
    raw = "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'API key not valid. ...'}}"
    err = translate_error(FakeApiError(400, raw))
    assert isinstance(err, InvalidApiKey)
    assert "GEMINI_API_KEY" in str(err)
    assert "{" not in str(err)


def test_스택트레이스를_그대로_노출하지_않는다():
    long_text = "x" * 5_000
    err = translate_error(FakeApiError(429, long_text))
    assert len(err.message) <= 500


# --- usage / 결과 --------------------------------------------------------------


class FakeMeta:
    def __init__(self, **kwargs: object) -> None:
        self.prompt_token_count = kwargs.get("prompt")
        self.candidates_token_count = kwargs.get("candidates")
        self.thoughts_token_count = kwargs.get("thoughts")
        self.total_token_count = kwargs.get("total")


def test_usage의_None을_0으로_다룬다():
    """실측 A-6 — 사고만 하고 잘리면 candidates_token_count 가 None 이다."""
    usage = Usage.from_metadata(FakeMeta(prompt=6, candidates=None, thoughts=47, total=53))
    assert (usage.input_tokens, usage.output_tokens) == (6, 0)
    assert (usage.thoughts_tokens, usage.total_tokens) == (47, 53)
    assert Usage.from_metadata(None).total_tokens == 0


def test_MAX_TOKENS는_잘림으로_표시된다():
    assert StreamResult(text="일부", finish_reason="MAX_TOKENS").truncated is True
    assert StreamResult(text="전부", finish_reason="STOP").truncated is False


def test_빈_응답_메시지가_사고_토큰을_설명한다():
    err = EmptyResponse(finish_reason="MAX_TOKENS", thoughts_tokens=47)
    assert "47" in str(err)
    assert "응답 모드" in str(err)


def test_thinking_config는_SDK_타입이다():
    cfg = build_config("gemma-4-31b-it", Settings(thinking_level="high", context_budget=3_000))
    assert isinstance(cfg.thinking_config, types.ThinkingConfig)
