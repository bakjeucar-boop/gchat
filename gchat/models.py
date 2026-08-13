"""모델 스펙 테이블.

계획서 1.1 / 1.2 / 1.3 / 1.4절의 단일 진실 원천이다.
UI · client · quota 는 이 테이블을 참조할 뿐 값을 하드코딩하지 않는다.
새 모델을 추가할 때는 이 파일의 MODELS 만 수정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 계열 식별자
FAMILY_GEMINI3 = "gemini3"
FAMILY_GEMMA4 = "gemma4"

# 계획서 1.4 / 2.3절 — 모든 한도의 90%를 실사용 상한으로 삼는다.
SAFETY_MARGIN = 0.9


@dataclass(frozen=True)
class RateLimits:
    """무료 티어 기준 요청 한도 (계획서 1.1절)."""

    rpm: int
    tpm: int
    rpd: int


@dataclass(frozen=True)
class ModelSpec:
    """모델 하나의 능력과 한도 (계획서 1.3절).

    주의: temperature / top_p / top_k / candidate_count 는 어느 모델에도
    전달하지 않기로 결정했으므로 (계획서 1.2절) 관련 필드를 두지 않는다.
    """

    id: str
    label: str
    family: str  # FAMILY_GEMINI3 | FAMILY_GEMMA4
    is_default: bool
    thinking_levels: tuple[str, ...]  # ("minimal","medium","high") | ("minimal","high")
    default_thinking_level: str  # 항상 "minimal"
    supports_system_instruction: bool
    supports_file_input: bool  # 계획서 2.9절 — v1 에서는 UI 게이팅용으로만 사용
    max_output_tokens: int  # 모델 상한
    context_window: int
    limits: RateLimits
    default_context_budget: int  # 계획서 1.4절
    default_max_output: int  # TPM 계산용 내부 상수. UI 에 노출하지 않는다 (계획서 2.6절)
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None


# 사고 수준의 표시 라벨은 계열별로 다르다 (계획서 1.3절).
THINKING_LABELS: dict[str, dict[str, str]] = {
    FAMILY_GEMINI3: {"minimal": "빠름", "medium": "보통", "high": "깊게"},
    FAMILY_GEMMA4: {"minimal": "사고 끄기", "high": "사고 켜기"},
}


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="gemini-3.5-flash-lite",
        label="Gemini 3.5 Flash-Lite",
        family=FAMILY_GEMINI3,
        is_default=True,
        thinking_levels=("minimal", "medium", "high"),
        default_thinking_level="minimal",
        supports_system_instruction=True,
        supports_file_input=True,
        # TODO(세션 2): max_output_tokens / context_window 는 실측 전 잠정값이다.
        max_output_tokens=65_536,
        context_window=1_048_576,
        limits=RateLimits(rpm=15, tpm=250_000, rpd=500),
        default_context_budget=32_000,
        default_max_output=4_096,
        # TODO(세션 2): 단가 확인 전까지 None. 비용 표시는 세션 4 이후 기능이다.
        price_in_per_mtok=None,
        price_out_per_mtok=None,
    ),
    ModelSpec(
        id="gemma-4-31b-it",
        label="Gemma 4 31B",
        family=FAMILY_GEMMA4,
        is_default=False,
        thinking_levels=("minimal", "high"),
        default_thinking_level="minimal",
        supports_system_instruction=True,
        # Gemma 4 도 Files API 로 이미지 입력은 지원하지만, TPM 16,000 과 예산 3,000
        # 아래에서는 첨부가 무의미하므로 계획서 2.9절 결정에 따라 게이팅한다.
        supports_file_input=False,
        # TODO(세션 2): 부록 B-5 — 문서마다 256K / 1M 로 상이해 실측 필요.
        max_output_tokens=8_192,
        context_window=262_144,
        limits=RateLimits(rpm=30, tpm=16_000, rpd=14_400),
        default_context_budget=3_000,
        default_max_output=2_048,
        price_in_per_mtok=None,
        price_out_per_mtok=None,
    ),
    ModelSpec(
        id="gemma-4-26b-a4b-it",
        label="Gemma 4 26B A4B",
        family=FAMILY_GEMMA4,
        is_default=False,
        thinking_levels=("minimal", "high"),
        default_thinking_level="minimal",
        supports_system_instruction=True,
        supports_file_input=False,
        # TODO(세션 2): 부록 B-5 — 실측 필요.
        max_output_tokens=8_192,
        context_window=262_144,
        limits=RateLimits(rpm=30, tpm=16_000, rpd=14_400),
        default_context_budget=3_000,
        default_max_output=2_048,
        price_in_per_mtok=None,
        price_out_per_mtok=None,
    ),
)

MODELS_BY_ID: dict[str, ModelSpec] = {spec.id: spec for spec in MODELS}


def get_model(model_id: str) -> ModelSpec:
    """모델 ID 로 스펙을 찾는다. 없으면 KeyError 대신 명확한 메시지를 낸다."""
    try:
        return MODELS_BY_ID[model_id]
    except KeyError:
        raise KeyError(f"알 수 없는 모델 ID: {model_id!r}") from None


def default_model() -> ModelSpec:
    """기본 선택 모델 (계획서 1.1절 — Gemini 3.5 Flash-Lite)."""
    for spec in MODELS:
        if spec.is_default:
            return spec
    raise RuntimeError("기본 모델이 정의되어 있지 않다 (MODELS 테이블 확인)")


def model_ids() -> tuple[str, ...]:
    """UI selectbox 등에 쓸 모델 ID 목록 (테이블 정의 순서)."""
    return tuple(spec.id for spec in MODELS)


def models_in_family(family: str) -> tuple[ModelSpec, ...]:
    return tuple(spec for spec in MODELS if spec.family == family)


def thinking_label(model_id: str, level: str) -> str:
    """사고 수준의 계열별 표시 라벨. 정의가 없으면 원값을 그대로 돌려준다."""
    spec = get_model(model_id)
    return THINKING_LABELS.get(spec.family, {}).get(level, level)


def resolve_thinking_level(model_id: str, level: str | None) -> str:
    """모델이 지원하지 않는 사고 수준은 minimal 로 되돌린다 (계획서 2.6.1절).

    계열이 바뀌어 medium 이 사라지는 경우가 대표적이다.
    """
    spec = get_model(model_id)
    if level in spec.thinking_levels:
        return level  # type: ignore[return-value]
    return spec.default_thinking_level


def max_request_tokens(model_id: str) -> int:
    """이 모델에서 한 요청이 쓸 수 있는 실사용 토큰 상한 (TPM 의 90%)."""
    return int(get_model(model_id).limits.tpm * SAFETY_MARGIN)
