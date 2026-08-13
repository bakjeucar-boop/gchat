"""한도 추적과 사전 판정 (계획서 2.3절).

핵심 규칙 — **TPM은 입력 토큰만 센다** (세션 2 실측, 계획서 1.4절).
슬라이딩 윈도우에 넣는 값은 언제나 `usage_metadata.prompt_token_count`이며,
출력(`candidates_token_count`)과 사고(`thoughts_token_count`)는 넣지 않는다.
`total_token_count`는 비용·표시용이다. 이걸 섞으면 예외도 안 나고 화면도
멀쩡한데 추적기만 과하게 세어 멀쩡한 요청을 막는다.

정직성 원칙 (계획서 2.3절)
- 이 추적기는 추정치다. 실제 할당량은 API 키 단위로 공유되고 앱이 재시작되면
  0으로 초기화되므로 실제보다 적게 셀 수 있다.
- 서버가 반환하는 429가 최종 진실이다. 추적기가 OK 라고 했는데 429가 나는
  상황을 정상 동작으로 간주해 처리한다.

의존 계층(계획서 3절)이 `quota → client` 이므로 이 모듈은 client 를 import 하지
않는다. 429 정보는 RateLimitInfo 프로토콜로 받는다.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from gchat.models import MODELS, ModelSpec, default_model, get_model

WINDOW = timedelta(seconds=60)

# 계획서 1.5절 — RPD 리셋은 태평양 시간 자정으로 알려져 있으나 세션 2에서
# 실측하지 못했다 (부록 B "아직 열려 있는 사항" 1번).
# TODO: 실사용 중 RPD 429 를 만나면 quotaId 와 retryDelay 를 로그로 남겨
# 이 가정을 확정할 것. apply_rate_limit() 이 그 정보를 받는 지점이다.
PACIFIC = ZoneInfo("America/Los_Angeles")

Clock = Callable[[], datetime]


@runtime_checkable
class RateLimitInfo(Protocol):
    """client.RateLimited 가 만족하는 모양. 계층을 지키려고 import 대신 프로토콜을 쓴다."""

    retry_after_s: float | None
    quota_id: str | None

    @property
    def is_daily(self) -> bool: ...


@runtime_checkable
class UsageLike(Protocol):
    """client.Usage 가 만족하는 모양. TPM 에 쓰는 것은 input_tokens 뿐이다."""

    input_tokens: int


class VerdictKind(StrEnum):
    OK = "OK"
    WAIT = "WAIT"
    DAILY_EXHAUSTED = "DAILY_EXHAUSTED"
    TOO_LARGE = "TOO_LARGE"


@dataclass(frozen=True)
class Verdict:
    """사전 판정 결과 (계획서 2.3절 표)."""

    kind: VerdictKind
    wait_s: float = 0.0
    reason: str = ""
    # 서버가 429 를 주면서 대기 시간을 알려주지 않은 상태인가.
    # 이때 wait_s 는 우리 창 계산값이며, 서버가 다시 막을 수 있다 (세션 3 결정).
    server_wait_unknown: bool = False

    @property
    def blocked(self) -> bool:
        return self.kind is not VerdictKind.OK

    def __str__(self) -> str:
        return self.reason or self.kind.value


@dataclass
class Gauges:
    """사이드바 진행바용 (계획서 2.3절)."""

    requests_in_window: int
    rpm_limit: int
    input_tokens_in_window: int
    tpm_limit: int
    daily_requests: int
    rpd_limit: int


def next_pacific_midnight(now: datetime) -> datetime:
    """now 이후 첫 태평양 시간 자정을 now 의 시간대로 돌려준다."""
    local = now.astimezone(PACIFIC)
    tomorrow = (local + timedelta(days=1)).date()
    midnight = datetime.combine(tomorrow, time.min, tzinfo=PACIFIC)
    return midnight.astimezone(now.tzinfo)


def _allowed(limit: int, margin: float) -> int:
    """한도의 margin 비율을 내림해 실사용 상한으로 삼는다.

    RPM 15 × 0.9 = 13.5 → 13회까지 허용하고 14번째를 막는다.
    """
    return math.floor(limit * margin)


class QuotaTracker:
    """모델 하나의 사용량. 시계는 주입받는다 (테스트에서 가짜 시계를 쓴다)."""

    def __init__(self, model_id: str, clock: Clock, *, margin: float = 0.9) -> None:
        self.spec: ModelSpec = get_model(model_id)
        self._clock = clock
        self._margin = margin
        self._requests: deque[datetime] = deque()
        # (시각, 입력 토큰). 입력만 넣는다 — TPM 은 입력 전용이다.
        self._tokens: deque[list] = deque()
        self._daily_requests = 0
        self._daily_reset_at = next_pacific_midnight(clock())
        self._blocked_until: datetime | None = None
        self._server_wait_unknown = False

    # --- 상한 -----------------------------------------------------------

    @property
    def model_id(self) -> str:
        return self.spec.id

    @property
    def allowed_rpm(self) -> int:
        return _allowed(self.spec.limits.rpm, self._margin)

    @property
    def allowed_tpm(self) -> int:
        return _allowed(self.spec.limits.tpm, self._margin)

    @property
    def allowed_rpd(self) -> int:
        return _allowed(self.spec.limits.rpd, self._margin)

    # --- 내부 상태 정리 ---------------------------------------------------

    def _prune(self, now: datetime) -> None:
        edge = now - WINDOW
        while self._requests and self._requests[0] <= edge:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] <= edge:
            self._tokens.popleft()
        if now >= self._daily_reset_at:
            self._daily_requests = 0
            self._daily_reset_at = next_pacific_midnight(now)
        if self._blocked_until is not None and now >= self._blocked_until:
            self._blocked_until = None
            self._server_wait_unknown = False

    def _tokens_in_window(self) -> int:
        return sum(entry[1] for entry in self._tokens)

    # --- 기록 -------------------------------------------------------------

    def record_sent(self, estimated_input_tokens: int) -> None:
        """요청을 보낸 직후 호출한다. 추정 입력 토큰으로 창을 채운다."""
        now = self._clock()
        self._prune(now)
        self._requests.append(now)
        self._tokens.append([now, max(0, estimated_input_tokens)])
        self._daily_requests += 1

    def record_usage(self, prompt_token_count: int | None) -> None:
        """응답 후 실제 입력 토큰으로 보정한다 (계획서 2.3절).

        `prompt_token_count` 만 받는다. 출력·사고 토큰을 여기에 넣으면 안 된다.
        필드가 None 으로 오는 경우가 실재하므로 0 으로 다룬다 (세션 2 A-6).
        가능하면 record_usage_from() 을 써서 필드 선택 자체를 이쪽에 맡길 것.
        """
        if not self._tokens:
            return
        self._tokens[-1][1] = max(0, prompt_token_count or 0)

    def record_usage_from(self, usage: UsageLike) -> None:
        """응답의 usage 객체에서 **입력 토큰만** 뽑아 기록한다.

        호출자가 total_token_count 를 넘기는 실수를 구조적으로 막는다.
        그 실수는 예외도 안 나고 화면도 멀쩡한데 추적기만 부풀어
        멀쩡한 요청을 막는 종류라 눈에 띄지 않는다.
        """
        self.record_usage(getattr(usage, "input_tokens", None))

    def apply_rate_limit(self, info: RateLimitInfo) -> None:
        """서버 429 로 추적기를 보정한다. 서버가 최종 진실이다.

        - 일일 한도(`...PerDay...`) → 당일 카운터를 상한까지 끌어올려 즉시 차단
        - 분당 한도 + retryDelay 있음 → 그 시각까지 차단
        - retryDelay 없음 → 차단 시각을 세울 수 없다. 우리 창 계산값을 쓰되
          "서버가 대기 시간을 알려주지 않았습니다"를 표시한다 (세션 3 결정)
        """
        now = self._clock()
        self._prune(now)
        if info.is_daily:
            self._daily_requests = max(self._daily_requests, self.allowed_rpd)
            return
        if info.retry_after_s is not None:
            self._blocked_until = now + timedelta(seconds=info.retry_after_s)
            self._server_wait_unknown = False
        else:
            self._server_wait_unknown = True

    # --- 판정 -------------------------------------------------------------

    def precheck(self, estimated_input_tokens: int) -> Verdict:
        """전송 전 판정 (계획서 2.3절 표).

        우선순위: TOO_LARGE → DAILY_EXHAUSTED → WAIT → OK.
        TOO_LARGE 는 기다려도 날짜가 바뀌어도 풀리지 않는 요청 자체의 문제라
        가장 먼저 알린다.
        """
        now = self._clock()
        self._prune(now)
        estimated = max(0, estimated_input_tokens)

        if estimated > self.allowed_tpm:
            return Verdict(
                kind=VerdictKind.TOO_LARGE,
                reason=(
                    f"이 입력은 {self.spec.label}의 요청당 한도"
                    f"({self.allowed_tpm:,} 토큰)를 넘습니다."
                ),
            )

        if self._daily_requests >= self.allowed_rpd:
            return Verdict(
                kind=VerdictKind.DAILY_EXHAUSTED,
                reason=(
                    f"{self.spec.label}의 오늘 사용량을 다 썼습니다 "
                    f"({self._daily_requests:,} / {self.spec.limits.rpd:,})."
                ),
            )

        wait = self._wait_needed(now, estimated)
        if wait > 0:
            return Verdict(
                kind=VerdictKind.WAIT,
                wait_s=wait,
                reason=self._wait_reason(wait),
                server_wait_unknown=self._server_wait_unknown,
            )
        if self._server_wait_unknown:
            # 창은 비었는데 서버는 429 를 준 상태. 막지는 않되 정직하게 알린다.
            return Verdict(
                kind=VerdictKind.OK,
                reason="서버가 대기 시간을 알려주지 않았습니다. 다시 막힐 수 있습니다.",
                server_wait_unknown=True,
            )
        return Verdict(kind=VerdictKind.OK)

    def _wait_reason(self, wait: float) -> str:
        seconds = math.ceil(wait)
        if self._server_wait_unknown:
            return (
                f"서버가 대기 시간을 알려주지 않았습니다. "
                f"자체 계산으로 약 {seconds}초 뒤 다시 시도할 수 있습니다."
            )
        return f"{self.spec.label}의 분당 한도에 도달했습니다. {seconds}초 후 보낼 수 있습니다."

    def _wait_needed(self, now: datetime, estimated: int) -> float:
        """전송 가능해질 때까지 남은 초. 추정이 아니라 계산으로 낸다."""
        waits = [0.0]

        if self._blocked_until is not None:
            waits.append((self._blocked_until - now).total_seconds())

        # RPM — 가장 오래된 요청이 창에서 빠지는 시각
        if len(self._requests) + 1 > self.allowed_rpm:
            over = len(self._requests) + 1 - self.allowed_rpm
            index = min(over - 1, len(self._requests) - 1)
            waits.append((self._requests[index] + WINDOW - now).total_seconds())

        # TPM — 필요한 만큼의 토큰 기록이 만료되는 시각
        used = self._tokens_in_window()
        if used + estimated > self.allowed_tpm:
            need = used + estimated - self.allowed_tpm
            freed = 0
            for stamp, tokens in self._tokens:
                freed += tokens
                if freed >= need:
                    waits.append((stamp + WINDOW - now).total_seconds())
                    break
            else:
                # 창을 다 비워도 모자란다. TOO_LARGE 가 먼저 걸러내므로 여기 오지 않는다.
                waits.append(WINDOW.total_seconds())

        return max(waits)

    def next_wait_s(self, estimated_input_tokens: int) -> float:
        """입력창 위 대기 예고용 (계획서 2.3절). 0이면 대기 없이 보낼 수 있다."""
        now = self._clock()
        self._prune(now)
        return max(0.0, self._wait_needed(now, max(0, estimated_input_tokens)))

    def gauges(self) -> Gauges:
        now = self._clock()
        self._prune(now)
        return Gauges(
            requests_in_window=len(self._requests),
            rpm_limit=self.spec.limits.rpm,
            input_tokens_in_window=self._tokens_in_window(),
            tpm_limit=self.spec.limits.tpm,
            daily_requests=self._daily_requests,
            rpd_limit=self.spec.limits.rpd,
        )

    def daily_remaining(self) -> int:
        now = self._clock()
        self._prune(now)
        return max(0, self.allowed_rpd - self._daily_requests)


@dataclass
class Recommendation:
    """차단 시 제시할 대안 (계획서 2.3절 모델 추천 로직)."""

    model_id: str | None
    wait_s: float = 0.0
    message: str = ""

    @property
    def available_now(self) -> bool:
        return self.model_id is not None and self.wait_s <= 0


class QuotaBook:
    """모델별 추적기 묶음. 추천 로직이 여러 모델을 함께 봐야 하므로 필요하다."""

    def __init__(self, clock: Clock, *, margin: float = 0.9) -> None:
        self._clock = clock
        self.trackers = {spec.id: QuotaTracker(spec.id, clock, margin=margin) for spec in MODELS}

    def tracker(self, model_id: str) -> QuotaTracker:
        return self.trackers[get_model(model_id).id]

    def precheck(self, model_id: str, estimated_input_tokens: int) -> Verdict:
        return self.tracker(model_id).precheck(estimated_input_tokens)

    def recommend(
        self, estimated_input_tokens: int, *, exclude: str | None = None
    ) -> Recommendation:
        """계획서 2.3절 판단 순서 그대로.

        1. RPD 여유가 있는 모델만 후보
        2. 그중 이번 요청이 TPM 여유 안에 들어가는 모델
        3. 여럿이면 기본 모델 우선
        4. 후보가 없으면 가장 빨리 풀리는 모델과 대기 시간을 안내
        """
        candidates: list[QuotaTracker] = [
            tracker
            for model_id, tracker in self.trackers.items()
            if model_id != exclude and tracker.daily_remaining() > 0
        ]
        if not candidates:
            return Recommendation(
                model_id=None,
                message="오늘 쓸 수 있는 모델이 없습니다. 내일 다시 시도하세요.",
            )

        ready = [
            tracker
            for tracker in candidates
            if tracker.precheck(estimated_input_tokens).kind is VerdictKind.OK
        ]
        if ready:
            chosen = _prefer_default(ready)
            return Recommendation(
                model_id=chosen.model_id,
                wait_s=0.0,
                message=(
                    f"지금 바로 보내려면 {chosen.spec.label}로 전환하세요 "
                    f"(오늘 잔여 {chosen.daily_remaining():,}회)."
                ),
            )

        # 4단계 — 가장 빨리 풀리는 모델. 요청 자체가 너무 큰 모델은 제외한다.
        usable = [
            tracker
            for tracker in candidates
            if tracker.precheck(estimated_input_tokens).kind is not VerdictKind.TOO_LARGE
        ]
        if not usable:
            return Recommendation(
                model_id=None,
                message="이 요청은 모든 모델의 요청당 한도를 넘습니다. 새 대화로 시작하세요.",
            )
        soonest = min(usable, key=lambda t: t.next_wait_s(estimated_input_tokens))
        wait = soonest.next_wait_s(estimated_input_tokens)
        return Recommendation(
            model_id=soonest.model_id,
            wait_s=wait,
            message=(
                f"가장 빨리 쓸 수 있는 모델은 {soonest.spec.label}이며 "
                f"약 {math.ceil(wait)}초 후입니다."
            ),
        )


def _prefer_default(trackers: Iterable[QuotaTracker]) -> QuotaTracker:
    """후보가 여럿이면 기본 모델을 고르고, 없으면 테이블 순서대로."""
    items = list(trackers)
    default_id = default_model().id
    for tracker in items:
        if tracker.model_id == default_id:
            return tracker
    return items[0]
