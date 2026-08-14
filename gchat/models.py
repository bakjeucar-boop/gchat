"""모델 스펙 테이블.

계획서 1.1 / 1.2 / 1.3 / 1.4절의 단일 진실 원천이다.
UI · client · quota 는 이 테이블을 참조할 뿐 값을 하드코딩하지 않는다.
새 모델을 추가할 때는 이 파일의 MODELS 만 수정한다.

값의 출처는 세션 2 실측이다 (docs/api_findings.md).
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
    # 계획서 2.6.2절 — 모델별 기본 시스템 인스트럭션. 없으면 "".
    # 숨겨서 몰래 덧붙이지 않는다. 컨트롤 바의 입력칸에 그대로 채워 보인다.
    default_system_instruction: str
    supports_file_input: bool  # 계획서 2.9절 — v1 에서는 UI 게이팅용으로만 사용
    max_output_tokens: int  # 모델 상한
    context_window: int
    limits: RateLimits
    default_context_budget: int  # 계획서 1.4절. TPM 을 소비하는 것은 이 값뿐이다
    default_max_output: int  # 내부 상수. UI 에 노출하지 않는다 (계획서 2.6절)
    # TODO(수동): API 로 조회할 수 없다. 비용 표시(계획서 2.8절)를 구현할 때
    # 공식 가격표를 보고 손으로 채운다.
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None


# 계획서 2.6.2절 — Gemma 는 자연 답변이 약 3,000토큰으로 장황하다 (세션 4 실사용).
# max_output_tokens 로는 줄일 수 없다. 그건 자르는 칼이지 짧게 쓰게 만드는 손잡이가
# 아니다. 생성 길이 자체를 바꾸는 유일한 수단이 시스템 인스트럭션이다.
#
# 세션 5 재개정: 첫 문구("2,000자 이내")는 출력을 435토큰까지 떨어뜨려 너무 짧았다.
# 목표는 1,000~1,500토큰(= 1,600~2,400자)이다. 상한만 주면 모델이 계속 아래로
# 내려가므로 **하한 지시("너무 짧게 줄이지 말고")를 명시한 것이 개정의 핵심**이다.
GEMMA_DEFAULT_INSTRUCTION = (
    "답변은 보통 1,600~2,400자 정도로 충분히 설명한다. 너무 짧게 줄이지 말고, "
    "필요한 근거와 예시는 포함한다. 다만 같은 내용을 반복하거나 형식적인 "
    "서론·맺음말은 넣지 않는다."
)

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
        # 자연 답변이 약 1,000토큰으로 적당하다. 개입하지 않는다 (계획서 2.6.2절).
        default_system_instruction="",
        supports_file_input=True,
        # 세션 2 실측 (models.get): input_token_limit / output_token_limit
        max_output_tokens=65_536,
        context_window=1_048_576,
        limits=RateLimits(rpm=15, tpm=250_000, rpd=500),
        default_context_budget=32_000,
        default_max_output=4_096,
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
        default_system_instruction=GEMMA_DEFAULT_INSTRUCTION,
        # Gemma 4 도 Files API 로 이미지 입력은 지원하지만, TPM 16,000 아래에서는
        # 첨부가 무의미하므로 계획서 2.9절 결정에 따라 게이팅한다.
        supports_file_input=False,
        # 세션 2 실측 — 컨텍스트 256K, 최대 출력 32,768 (문서의 1M 설은 사실이 아니다)
        max_output_tokens=32_768,
        context_window=262_144,
        limits=RateLimits(rpm=30, tpm=16_000, rpd=14_400),
        # 세션 4 실사용으로 3,000 → 9,000 상향 (계획서 1.4·1.5절).
        # TPM 은 실제로 걸리지 않았다 — 6턴 내내 대기 0회, 창 사용률 29%.
        # 예산 9,000 의 TPM상 최소 간격 37.5초 < 출력 1,536 의 생성 시간 약 50초.
        default_context_budget=9_000,
        # 768 → 1,536 → 2,048 (세션 5 후 재상향). 목표 답변이 1,000~1,500토큰이라
        # 1,536 은 목표 상단과 너무 가까워 잘림이 생겼다. 이 값은 안전장치이고
        # 실제 길이를 정하는 것은 default_system_instruction 이다 (계획서 2.6.2절).
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
        default_system_instruction=GEMMA_DEFAULT_INSTRUCTION,
        supports_file_input=False,
        # 세션 2 실측 — 31B 와 동일
        max_output_tokens=32_768,
        context_window=262_144,
        limits=RateLimits(rpm=30, tpm=16_000, rpd=14_400),
        default_context_budget=9_000,
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
    """한 요청이 쓸 수 있는 실사용 **입력** 토큰 상한 (TPM 의 90%).

    세션 2 실측: TPM 은 입력 토큰만 센다 (429 메트릭 input_token_count).
    출력과 사고 토큰은 TPM 을 소비하지 않으므로 이 계산에 넣지 않는다.
    """
    return int(get_model(model_id).limits.tpm * SAFETY_MARGIN)


def requests_per_minute_at(model_id: str, context_budget: int) -> float:
    """주어진 컨텍스트 예산에서 분당 몇 번 보낼 수 있는가.

    TPM 과 RPM 중 먼저 걸리는 쪽을 돌려준다 (계획서 1.4 / 1.5절).
    """
    spec = get_model(model_id)
    if context_budget <= 0:
        return float(spec.limits.rpm)
    by_tpm = max_request_tokens(model_id) / context_budget
    return min(by_tpm, float(spec.limits.rpm))
