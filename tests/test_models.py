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
    GEMMA_CODING_INSTRUCTION,
    GEMMA_DEFAULT_INSTRUCTION,
    MODELS,
    MODELS_BY_ID,
    PURPOSE_CODING,
    PURPOSE_CUSTOM,
    PURPOSE_GENERAL,
    PURPOSE_INSTRUCTIONS,
    SAFETY_MARGIN,
    THINKING_LABELS,
    ModelSpec,
    compose_instruction,
    default_model,
    get_model,
    length_instruction,
    max_output_for,
    max_request_tokens,
    model_ids,
    models_in_family,
    requests_per_minute_at,
    resolve_thinking_level,
    thinking_label,
    tpm_boundary_budget,
)

# 계획서 1.1절 표
GEMINI = "gemini-3.5-flash-lite"
GEMMA = "gemma-4-31b-it"

EXPECTED_LIMITS = {
    "gemini-3.5-flash-lite": (15, 250_000, 500),
    "gemma-4-31b-it": (30, 16_000, 14_400),
    "gemma-4-26b-a4b-it": (30, 16_000, 14_400),
}

# 계획서 1.4절 표 — (기본 컨텍스트 예산, 기본 최대 출력)
# Gemma 는 세션 4 실사용 후 3,000·768 에서 상향됐다.
EXPECTED_BUDGETS = {
    "gemini-3.5-flash-lite": (32_000, 4_096),
    "gemma-4-31b-it": (9_000, 2_048),
    "gemma-4-26b-a4b-it": (9_000, 2_048),
}

# 세션 2 실측 (models.get) — (context_window, max_output_tokens)
MEASURED_LIMITS = {
    "gemini-3.5-flash-lite": (1_048_576, 65_536),
    "gemma-4-31b-it": (262_144, 32_768),
    "gemma-4-26b-a4b-it": (262_144, 32_768),
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


@pytest.mark.parametrize("model_id", sorted(MEASURED_LIMITS))
def test_컨텍스트_윈도우와_최대출력이_실측값과_일치한다(model_id: str):
    """세션 2 실측값 (docs/api_findings.md A-1)."""
    spec = get_model(model_id)
    assert (spec.context_window, spec.max_output_tokens) == MEASURED_LIMITS[model_id]


@pytest.mark.parametrize("spec", MODELS, ids=lambda s: s.id)
def test_컨텍스트_예산이_TPM의_90퍼센트를_넘지_않는다(spec: ModelSpec):
    """계획서 1.4절 — TPM 을 소비하는 것은 입력, 즉 컨텍스트 예산뿐이다.

    세션 2 실측으로 "예산 + 최대 출력" 이 아니라 "예산" 단독 기준이 됐다
    (docs/api_findings.md B절).
    """
    assert spec.default_context_budget <= spec.limits.tpm * SAFETY_MARGIN, (
        f"{spec.id}: 예산 {spec.default_context_budget:,} 은 "
        f"TPM {spec.limits.tpm:,} 의 90%를 넘는다"
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


def test_Gemini는_기본_인스트럭션이_없다():
    """자연 답변 길이가 적당해 개입하지 않는다 (계획서 2.6.2절)."""
    assert get_model("gemini-3.5-flash-lite").default_system_instruction == ""


@pytest.mark.parametrize("spec", models_in_family(FAMILY_GEMMA4), ids=lambda s: s.id)
def test_Gemma는_간결하게_쓰라는_기본_인스트럭션을_갖는다(spec: ModelSpec):
    """계획서 2.6.2절 — 자연 답변 약 3,000토큰을 목표 범위로 끌어오는 유일한 수단."""
    instruction = spec.default_system_instruction
    assert instruction == GEMMA_DEFAULT_INSTRUCTION
    # 세션 5 재개정의 핵심은 하한 지시다. 상한만 주면 모델이 계속 아래로 내려간다.
    assert "너무 짧게 줄이지 말고" in instruction
    assert "1,600~2,400자" in instruction  # = 1,000~1,500 토큰 (2.2절 상수 1.6자/토큰)


def test_출력_상한은_용도별이다():
    """계획서 1.4절 — 범용·커스텀은 모델 기본값, 코딩만 올린다."""
    assert max_output_for(GEMMA, PURPOSE_GENERAL) == 2_048
    assert max_output_for(GEMMA, PURPOSE_CUSTOM) == 2_048
    assert max_output_for(GEMMA, PURPOSE_CODING) == 4_096
    # Gemini 는 기본값이 이미 4,096 이라 코딩에서도 그대로다
    assert max_output_for(GEMINI, PURPOSE_GENERAL) == 4_096
    assert max_output_for(GEMINI, PURPOSE_CODING) == 4_096


def test_용도별_상한은_모델_자체_상한을_넘지_않는다():
    for spec in MODELS:
        for purpose in (PURPOSE_GENERAL, PURPOSE_CODING, PURPOSE_CUSTOM):
            assert max_output_for(spec.id, purpose) <= spec.max_output_tokens


def test_TPM_병목_경계를_유도한다():
    """계획서 1.5절 — 경계 = (답변 토큰 ÷ 초당 토큰) × (가용 TPM ÷ 60)."""
    # Gemma: (1,301 ÷ 34.2) × (14,400 ÷ 60) = 38.0초 × 240 ≈ 9,100
    assert tpm_boundary_budget(GEMMA) == pytest.approx(9_100, abs=60)
    # 답변이 짧아지면 경계도 내려간다 (세션 5 과소 생성 시절 435토큰 → 약 3,100)
    assert tpm_boundary_budget(GEMMA, 435) == pytest.approx(3_050, abs=60)
    # 상한을 다 채우면 슬라이더 상한에 닿는다
    assert tpm_boundary_budget(GEMMA, 2_048) >= 14_000
    # Gemini 는 TPM 이 넓어 경계가 예산 범위를 한참 넘는다
    assert tpm_boundary_budget(GEMINI) > 100_000


def test_코딩_용도에서는_글자수_목표를_주지_않는다():
    """세션 6 실사용 — 1,600~2,400자에 맞추려다 코드가 끊겼다."""
    coding = compose_instruction("gemma-4-31b-it", PURPOSE_CODING)
    assert "1,600~2,400자" not in coding
    assert GEMMA_CODING_INSTRUCTION in coding
    assert PURPOSE_INSTRUCTIONS[PURPOSE_CODING] in coding

    # 범용은 그대로 글자 수 목표를 준다
    general = compose_instruction("gemma-4-31b-it", PURPOSE_GENERAL)
    assert GEMMA_DEFAULT_INSTRUCTION in general


def test_Gemini에는_길이_지시를_주지_않는다():
    for purpose in (PURPOSE_GENERAL, PURPOSE_CODING):
        assert length_instruction("gemini-3.5-flash-lite", purpose) == ""


def test_기본_인스트럭션은_모델_테이블에만_있다():
    """UI 가 값을 하드코딩하지 않도록 테이블이 단일 출처다 (CLAUDE.md 원칙)."""
    assert all(isinstance(spec.default_system_instruction, str) for spec in MODELS)


def test_요청당_토큰_상한은_TPM의_90퍼센트다():
    assert max_request_tokens("gemma-4-31b-it") == 14_400
    assert max_request_tokens("gemini-3.5-flash-lite") == 225_000


def test_예산에_따른_분당_가능_횟수():
    """계획서 1.5절 표의 근거 계산 (TPM 은 입력만 센다)."""
    # Gemma: 가용 14,400 / 예산 3,000 = 4.8회. RPM 30 보다 TPM 이 먼저 걸린다.
    assert requests_per_minute_at("gemma-4-31b-it", 3_000) == pytest.approx(4.8)
    assert requests_per_minute_at("gemma-4-31b-it", 12_000) == pytest.approx(1.2)
    # Gemini: 225,000 / 32,000 = 7.03회. 역시 RPM 15 보다 TPM 이 먼저다.
    assert requests_per_minute_at("gemini-3.5-flash-lite", 32_000) == pytest.approx(7.03, abs=0.01)
    # 예산이 아주 작으면 RPM 이 상한이 된다.
    assert requests_per_minute_at("gemma-4-31b-it", 100) == 30.0


def test_알_수_없는_모델은_명확한_오류를_낸다():
    with pytest.raises(KeyError, match="알 수 없는 모델 ID"):
        get_model("gemini-9-ultra")
