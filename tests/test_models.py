"""models.py 테이블 정합성 테스트.

값의 출처는 계획서 1.1 / 1.2 / 1.4절이다. 이 테스트가 깨지면 테이블이
계획서에서 벗어난 것이므로, 테스트를 고치기 전에 계획서를 먼저 확인한다.
"""

from __future__ import annotations

import dataclasses

import pytest

from gchat.models import (
    FAMILY_GEMINI3,
    FAMILY_GEMMA4,
    MODELS,
    MODELS_BY_ID,
    SAFETY_MARGIN,
    THINKING_LABELS,
    ModelSpec,
    default_model,
    get_model,
    max_request_tokens,
    model_ids,
    models_in_family,
    resolve_thinking_level,
    thinking_label,
)

# 계획서 1.1절 표
EXPECTED_LIMITS = {
    "gemini-3.5-flash-lite": (15, 250_000, 500),
    "gemma-4-31b-it": (30, 16_000, 14_400),
    "gemma-4-26b-a4b-it": (30, 16_000, 14_400),
}

# 계획서 1.4절 표 — (기본 컨텍스트 예산, 기본 최대 출력)
EXPECTED_BUDGETS = {
    "gemini-3.5-flash-lite": (32_000, 4_096),
    "gemma-4-31b-it": (3_000, 2_048),
    "gemma-4-26b-a4b-it": (3_000, 2_048),
}

# 계획서 1.2절 표
EXPECTED_THINKING_LEVELS = {
    "gemini-3.5-flash-lite": ("minimal", "medium", "high"),
    "gemma-4-31b-it": ("minimal", "high"),
    "gemma-4-26b-a4b-it": ("minimal", "high"),
}

EXPECTED_FAMILIES = {
    "gemini-3.5-flash-lite": FAMILY_GEMINI3,
    "gemma-4-31b-it": FAMILY_GEMMA4,
    "gemma-4-26b-a4b-it": FAMILY_GEMMA4,
}


def test_모델은_계획서의_3개다():
    assert set(model_ids()) == set(EXPECTED_LIMITS)
    assert len(MODELS) == 3


def test_모델_id는_중복되지_않는다():
    assert len(MODELS_BY_ID) == len(MODELS)


def test_기본_모델은_정확히_하나다():
    defaults = [spec for spec in MODELS if spec.is_default]
    assert len(defaults) == 1


def test_기본_모델은_gemini_3_5_flash_lite다():
    assert default_model().id == "gemini-3.5-flash-lite"
    assert default_model().label == "Gemini 3.5 Flash-Lite"


@pytest.mark.parametrize("model_id", sorted(EXPECTED_LIMITS))
def test_한도가_계획서_1_1절과_일치한다(model_id: str):
    limits = get_model(model_id).limits
    assert (limits.rpm, limits.tpm, limits.rpd) == EXPECTED_LIMITS[model_id]


@pytest.mark.parametrize("model_id", sorted(EXPECTED_BUDGETS))
def test_예산과_최대출력이_계획서_1_4절과_일치한다(model_id: str):
    spec = get_model(model_id)
    assert (spec.default_context_budget, spec.default_max_output) == EXPECTED_BUDGETS[model_id]


@pytest.mark.parametrize("model_id", sorted(EXPECTED_FAMILIES))
def test_계열이_계획서와_일치한다(model_id: str):
    assert get_model(model_id).family == EXPECTED_FAMILIES[model_id]


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.id)
def test_요청당_최대소비가_TPM의_90퍼센트를_넘지_않는다(spec: ModelSpec):
    """계획서 1.4절 — 요청당 소비 = 컨텍스트 예산 + 최대 출력."""
    worst_case = spec.default_context_budget + spec.default_max_output
    assert worst_case <= spec.limits.tpm * SAFETY_MARGIN, (
        f"{spec.id}: {worst_case:,} 토큰은 TPM {spec.limits.tpm:,} 의 90%를 넘는다"
    )


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.id)
def test_기본_컨텍스트_예산은_컨텍스트_윈도우_안에_있다(spec: ModelSpec):
    assert spec.default_context_budget < spec.context_window


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.id)
def test_기본_최대출력은_모델_상한_이하다(spec: ModelSpec):
    assert spec.default_max_output <= spec.max_output_tokens


@pytest.mark.parametrize("model_id", sorted(EXPECTED_THINKING_LEVELS))
def test_사고_수준_선택지가_계획서_1_2절과_일치한다(model_id: str):
    assert get_model(model_id).thinking_levels == EXPECTED_THINKING_LEVELS[model_id]


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.id)
def test_기본_사고_수준은_항상_minimal이고_선택지에_들어있다(spec: ModelSpec):
    assert spec.default_thinking_level == "minimal"
    assert spec.default_thinking_level in spec.thinking_levels


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.id)
def test_모든_사고_수준에_표시_라벨이_있다(spec: ModelSpec):
    labels = THINKING_LABELS[spec.family]
    for level in spec.thinking_levels:
        assert level in labels
        assert labels[level]


def test_라벨_매핑에_계열이_빠짐없이_있다():
    assert set(THINKING_LABELS) == {spec.family for spec in MODELS}


def test_사고_수준_라벨은_계열별로_다르다():
    assert thinking_label("gemini-3.5-flash-lite", "minimal") == "빠름"
    assert thinking_label("gemma-4-31b-it", "minimal") == "사고 끄기"
    assert thinking_label("gemma-4-31b-it", "high") == "사고 켜기"


def test_지원하지_않는_사고_수준은_minimal로_되돌아간다():
    """계획서 2.6.1절 — Gemini medium → Gemma 전환."""
    assert resolve_thinking_level("gemma-4-31b-it", "medium") == "minimal"
    assert resolve_thinking_level("gemma-4-31b-it", "high") == "high"
    assert resolve_thinking_level("gemini-3.5-flash-lite", "medium") == "medium"
    assert resolve_thinking_level("gemini-3.5-flash-lite", None) == "minimal"


def test_샘플링_파라미터_필드는_존재하지_않는다():
    """계획서 1.2절 결정 — 어느 모델에도 전달하지 않으므로 필드 자체를 두지 않는다."""
    field_names = {f.name for f in dataclasses.fields(ModelSpec)}
    forbidden = {"temperature", "top_p", "top_k", "candidate_count", "supports_sampling"}
    assert not (field_names & forbidden)


def test_스펙은_불변이다():
    spec = default_model()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.is_default = False  # type: ignore[misc]


def test_계열_조회():
    assert len(models_in_family(FAMILY_GEMMA4)) == 2
    assert len(models_in_family(FAMILY_GEMINI3)) == 1


def test_파일_첨부는_gemini에서만_활성화된다():
    """계획서 2.9절 결정 — Gemma 는 TPM 제약으로 첨부를 게이팅한다."""
    assert get_model("gemini-3.5-flash-lite").supports_file_input is True
    for spec in models_in_family(FAMILY_GEMMA4):
        assert spec.supports_file_input is False


def test_모든_모델이_시스템_인스트럭션을_지원한다():
    """계획서 부록 B — Gemma 4 도 시스템 인스트럭션을 지원한다."""
    assert all(spec.supports_system_instruction for spec in MODELS)


def test_요청당_토큰_상한은_TPM의_90퍼센트다():
    assert max_request_tokens("gemma-4-31b-it") == 14_400
    assert max_request_tokens("gemini-3.5-flash-lite") == 225_000


def test_알_수_없는_모델은_명확한_오류를_낸다():
    with pytest.raises(KeyError, match="알 수 없는 모델 ID"):
        get_model("gemini-9-ultra")
