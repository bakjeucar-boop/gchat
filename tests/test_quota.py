"""한도 추적 테스트 — 계획서 2.3절 시나리오를 가짜 시계로 재현한다.

네트워크를 쓰지 않는다. 시계만 앞으로 돌린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from gchat.quota import (
    PACIFIC,
    QuotaBook,
    QuotaTracker,
    VerdictKind,
    next_pacific_midnight,
)

GEMINI = "gemini-3.5-flash-lite"
GEMMA = "gemma-4-31b-it"
GEMMA26 = "gemma-4-26b-a4b-it"


class FakeClock:
    """호출할 때마다 같은 시각을 주고, 시험이 직접 앞으로 돌린다."""

    def __init__(self, start: datetime | None = None) -> None:
        # 태평양 자정과 멀리 떨어진 시각으로 시작해 일일 리셋이 끼어들지 않게 한다.
        self.now = start or datetime(2026, 8, 13, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class FakeUsage:
    """client.Usage 와 같은 모양. TPM 에 들어가야 하는 것은 input_tokens 뿐이다."""

    input_tokens: int = 1_200
    output_tokens: int = 5_000
    thoughts_tokens: int = 700
    total_tokens: int = 6_900


@dataclass
class FakeRateLimited:
    """client.RateLimited 와 같은 모양 (quota 는 client 를 import 하지 않는다)."""

    retry_after_s: float | None = None
    quota_id: str | None = None

    @property
    def is_daily(self) -> bool:
        return bool(self.quota_id and "PerDay" in self.quota_id)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# --- 상한 계산 ----------------------------------------------------------------


def test_상한은_한도의_90퍼센트를_내림한다(clock: FakeClock):
    gemini = QuotaTracker(GEMINI, clock)
    assert gemini.allowed_rpm == 13  # 15 × 0.9 = 13.5 → 13
    assert gemini.allowed_tpm == 225_000
    assert gemini.allowed_rpd == 450

    gemma = QuotaTracker(GEMMA, clock)
    assert gemma.allowed_rpm == 27
    assert gemma.allowed_tpm == 14_400
    assert gemma.allowed_rpd == 12_960


# --- 시나리오 1: 여유 있음 ------------------------------------------------------


def test_시나리오1_여유가_있으면_OK(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    assert tracker.precheck(1_000).kind is VerdictKind.OK
    assert tracker.next_wait_s(1_000) == 0


# --- 시나리오 2: RPM 초과 -------------------------------------------------------


def test_시나리오2_분당_요청_초과시_WAIT(clock: FakeClock):
    tracker = QuotaTracker(GEMINI, clock)
    for _ in range(13):  # 허용치까지 채운다
        tracker.record_sent(10)
        clock.advance(1)
    verdict = tracker.precheck(10)
    assert verdict.kind is VerdictKind.WAIT
    # 첫 요청은 13초 전이므로 60 - 13 = 47초 남았다
    assert verdict.wait_s == pytest.approx(47.0)
    assert "잠깐 쉬게" in verdict.reason and "47초" in verdict.reason


def test_시나리오2_창이_비면_다시_OK(clock: FakeClock):
    tracker = QuotaTracker(GEMINI, clock)
    for _ in range(13):
        tracker.record_sent(10)
    assert tracker.precheck(10).kind is VerdictKind.WAIT
    clock.advance(61)
    assert tracker.precheck(10).kind is VerdictKind.OK


# --- 시나리오 3: TPM 초과 -------------------------------------------------------


def test_시나리오3_분당_토큰_초과시_WAIT(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(14_000)
    clock.advance(10)
    verdict = tracker.precheck(1_000)  # 14,000 + 1,000 > 14,400
    assert verdict.kind is VerdictKind.WAIT
    assert verdict.wait_s == pytest.approx(50.0)  # 첫 기록이 60초를 채울 때까지


def test_시나리오3_필요한_만큼만_기다린다(clock: FakeClock):
    """여러 기록이 쌓였으면 필요한 양이 만료되는 시각까지만 기다린다."""
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(5_000)  # t=0
    clock.advance(10)
    tracker.record_sent(5_000)  # t=10
    clock.advance(10)
    tracker.record_sent(4_000)  # t=20, 누적 14,000
    verdict = tracker.precheck(1_000)  # 14,000 + 1,000 > 14,400 → 600 부족
    # 첫 기록(5,000)만 빠지면 충분하다. t=0 기록은 t=60 에 만료 → 지금(t=20)부터 40초
    assert verdict.kind is VerdictKind.WAIT
    assert verdict.wait_s == pytest.approx(40.0)


# --- 시나리오 4: RPD 소진 -------------------------------------------------------


def test_시나리오4_일일_소진시_DAILY_EXHAUSTED(clock: FakeClock):
    tracker = QuotaTracker(GEMINI, clock)
    for _ in range(450):
        tracker.record_sent(10)
        clock.advance(0.1)
    verdict = tracker.precheck(10)
    assert verdict.kind is VerdictKind.DAILY_EXHAUSTED
    assert "오늘" in verdict.reason and "다 썼습니다" in verdict.reason
    assert tracker.daily_remaining() == 0


def test_시나리오4_태평양_자정에_리셋된다(clock: FakeClock):
    tracker = QuotaTracker(GEMINI, clock)
    for _ in range(450):
        tracker.record_sent(10)
    assert tracker.precheck(10).kind is VerdictKind.DAILY_EXHAUSTED

    reset_at = next_pacific_midnight(clock.now)
    clock.now = reset_at + timedelta(seconds=1)
    assert tracker.precheck(10).kind is VerdictKind.OK
    assert tracker.daily_remaining() == 450


def test_태평양_자정_계산():
    # 2026-08-13 12:00 UTC = 태평양 05:00 (PDT). 다음 자정은 8/14 00:00 PDT = 07:00 UTC
    now = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    midnight = next_pacific_midnight(now)
    assert midnight.astimezone(PACIFIC).hour == 0
    assert midnight > now
    assert (midnight - now) < timedelta(days=1)


# --- 시나리오 5: 요청이 너무 큼 --------------------------------------------------


def test_시나리오5_단일_요청이_한도를_넘으면_TOO_LARGE(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    verdict = tracker.precheck(20_000)  # TPM 90% = 14,400
    assert verdict.kind is VerdictKind.TOO_LARGE
    assert "너무 깁니다" in verdict.reason


def test_시나리오5_TOO_LARGE가_다른_판정보다_먼저다(clock: FakeClock):
    """기다려도 날짜가 바뀌어도 풀리지 않으므로 가장 먼저 알린다."""
    tracker = QuotaTracker(GEMMA, clock)
    for _ in range(12_960):  # 일일 소진까지 채운다
        tracker.record_sent(1)
    assert tracker.precheck(20_000).kind is VerdictKind.TOO_LARGE
    assert tracker.precheck(100).kind is VerdictKind.DAILY_EXHAUSTED


# --- 시나리오 6: 서버 429 (분당) -------------------------------------------------


def test_시나리오6_분당_429는_서버_시각까지_기다린다(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    assert tracker.precheck(100).kind is VerdictKind.OK  # 추적기는 여유 있다고 봤다

    tracker.apply_rate_limit(
        FakeRateLimited(
            retry_after_s=31.7,
            quota_id="GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
        )
    )
    verdict = tracker.precheck(100)
    assert verdict.kind is VerdictKind.WAIT
    assert verdict.wait_s == pytest.approx(31.7)

    clock.advance(32)
    assert tracker.precheck(100).kind is VerdictKind.OK


def test_시나리오6_추적기가_OK인데_429가_와도_정상_처리한다(clock: FakeClock):
    """계획서 2.3절 정직성 원칙 — 서버가 최종 진실이다."""
    tracker = QuotaTracker(GEMMA, clock)
    tracker.apply_rate_limit(FakeRateLimited(retry_after_s=10, quota_id="...PerMinute..."))
    assert tracker.precheck(1).kind is VerdictKind.WAIT


def test_retryDelay가_없는_429는_자체_계산값을_쓴다(clock: FakeClock):
    """세션 3 결정 — 예외로 취급하지 않고 정상 경로로 처리한다."""
    tracker = QuotaTracker(GEMMA, clock)
    tracker.apply_rate_limit(FakeRateLimited(retry_after_s=None, quota_id=None))

    verdict = tracker.precheck(100)  # 우리 창은 비어 있다
    assert verdict.kind is VerdictKind.OK  # 막지 않는다
    assert verdict.server_wait_unknown is True
    assert "또 막힐 수 있습니다" in verdict.reason


def test_retryDelay가_없어도_창이_찼으면_그_계산값으로_기다린다(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(14_000)
    tracker.apply_rate_limit(FakeRateLimited(retry_after_s=None, quota_id=None))
    verdict = tracker.precheck(1_000)
    assert verdict.kind is VerdictKind.WAIT
    assert verdict.wait_s == pytest.approx(60.0)
    assert verdict.server_wait_unknown is True
    assert "잠시 쉬어야 합니다" in verdict.reason


# --- 시나리오 7: 서버 429 (일일) -------------------------------------------------


def test_시나리오7_일일_429는_즉시_소진_처리한다(clock: FakeClock):
    tracker = QuotaTracker(GEMINI, clock)
    tracker.apply_rate_limit(
        FakeRateLimited(
            retry_after_s=None,
            quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier",
        )
    )
    assert tracker.precheck(10).kind is VerdictKind.DAILY_EXHAUSTED
    assert tracker.daily_remaining() == 0


# --- 회귀 방지 1: TPM 은 입력만 센다 ---------------------------------------------


def test_출력_크기는_TPM_판정에_영향을_주지_않는다(clock: FakeClock):
    """이번 세션에서 가장 회귀하기 쉬운 지점 (계획서 1.4절).

    같은 입력, 다른 max_output 두 요청은 완전히 같게 취급되어야 한다.
    """
    a = QuotaTracker(GEMMA, clock)
    b = QuotaTracker(GEMMA, clock)

    a.record_sent(3_000)
    a.record_usage(3_000)  # 짧은 응답
    b.record_sent(3_000)
    b.record_usage(3_000)  # 긴 응답 — 그래도 기록되는 값은 입력뿐이다

    assert a.gauges().input_tokens_in_window == b.gauges().input_tokens_in_window
    assert a.precheck(3_000) == b.precheck(3_000)


def test_record_usage는_실측_입력으로_추정치를_대체한다(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(1_000)  # 추정치
    tracker.record_usage(1_200)  # 실측 입력
    assert tracker.gauges().input_tokens_in_window == 1_200


def test_usage_객체에서_입력_토큰만_뽑는다(clock: FakeClock):
    """client.Usage 를 통째로 넘겨도 창에는 입력만 들어가야 한다.

    출력 5,000 · 사고 700 이 섞여 들어가면 total 6,900 으로 창이 부풀어
    멀쩡한 요청을 막는다. 필드 선택을 quota 쪽에 두어 구조적으로 막는다.
    """
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(1_000)
    tracker.record_usage_from(FakeUsage())
    assert tracker.gauges().input_tokens_in_window == 1_200


def test_client의_Usage와_모양이_맞는다(clock: FakeClock):
    """계층상 quota 는 client 를 import 하지 않는다. 모양만 맞으면 된다."""
    from gchat.client import Usage
    from gchat.quota import UsageLike

    usage = Usage(input_tokens=800, output_tokens=4_000, thoughts_tokens=300, total_tokens=5_100)
    assert isinstance(usage, UsageLike)

    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(999)
    tracker.record_usage_from(usage)
    assert tracker.gauges().input_tokens_in_window == 800


def test_창의_기준_시각은_전송_시각이지_응답_시각이_아니다(clock: FakeClock):
    """Gemma 는 긴 응답 하나에 45초가 걸린다 (세션 2 실측).

    응답이 끝난 시각으로 기록하면 창이 45초 밀려, 이미 풀린 요청을 계속 막는다.
    예외도 안 나고 화면도 멀쩡한데 그냥 느려지는 결함이다.
    """
    tracker = QuotaTracker(GEMMA, clock)
    entry = tracker.record_sent(14_000)  # t=0 에 전송
    clock.advance(45)  # 스트리밍에 45초
    tracker.record_usage_from(FakeUsage(input_tokens=14_000), entry)

    # t=45. 전송 시각 기준이면 15초 뒤 만료된다.
    assert tracker.next_wait_s(1_000) == pytest.approx(15.0)

    clock.advance(15)  # t=60 — 전송 후 60초
    assert tracker.precheck(1_000).kind is VerdictKind.OK


def test_보정이_늦어도_창이_밀리지_않는다(clock: FakeClock):
    """보정은 값만 갱신한다. 시각은 record_sent 시점 그대로여야 한다."""
    tracker = QuotaTracker(GEMMA, clock)
    entry = tracker.record_sent(1_000)
    clock.advance(50)
    tracker.record_usage_from(FakeUsage(input_tokens=9_999), entry)

    assert tracker.gauges().input_tokens_in_window == 9_999
    clock.advance(11)  # 전송 후 61초
    assert tracker.gauges().input_tokens_in_window == 0  # 시각이 밀렸다면 남아 있다


def test_스트림이_창보다_길면_보정을_버린다(clock: FakeClock):
    """60초를 넘긴 스트림의 기록은 이미 만료됐다.

    핸들 없이 마지막 기록을 덮어쓰면 그 사이 들어온 다른 요청을 부풀린다.
    """
    tracker = QuotaTracker(GEMMA, clock)
    old = tracker.record_sent(1_000)  # t=0
    clock.advance(70)  # 스트림이 70초 걸렸다 — 이 기록은 만료
    fresh = tracker.record_sent(500)  # t=70 에 다른 요청
    tracker.record_usage_from(FakeUsage(input_tokens=13_000), old)  # 뒤늦은 보정

    assert tracker.gauges().input_tokens_in_window == 500  # 새 기록은 그대로다
    assert fresh.tokens == 500


def test_핸들을_안_넘기면_마지막_기록을_보정한다(clock: FakeClock):
    """단일 요청 흐름에서는 핸들 없이도 동작한다 (기존 호출부 호환)."""
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(1_000)
    tracker.record_usage(1_234)
    assert tracker.gauges().input_tokens_in_window == 1_234


def test_usage가_None이면_0으로_다룬다(clock: FakeClock):
    """세션 2 A-6 — 사고만 하고 잘리면 필드가 None 으로 온다."""
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(500)
    tracker.record_usage(None)
    assert tracker.gauges().input_tokens_in_window == 0


# --- 회귀 방지 2: 1.5절 표와 일치 -------------------------------------------------


def test_Gemma_3000토큰_요청은_분당_4회까지다(clock: FakeClock):
    """계획서 1.5절 — 예산 3,000에서 4.8회/분. 5회째에서 막혀야 한다."""
    tracker = QuotaTracker(GEMMA, clock)
    for i in range(4):
        assert tracker.precheck(3_000).kind is VerdictKind.OK, f"{i + 1}번째가 막혔다"
        tracker.record_sent(3_000)
        clock.advance(1)
    verdict = tracker.precheck(3_000)  # 12,000 + 3,000 > 14,400
    assert verdict.kind is VerdictKind.WAIT


def test_Gemini_32000토큰_요청은_분당_7회까지다(clock: FakeClock):
    """계획서 1.4절 — 225,000 / 32,000 = 7.03회."""
    tracker = QuotaTracker(GEMINI, clock)
    for i in range(7):
        assert tracker.precheck(32_000).kind is VerdictKind.OK, f"{i + 1}번째가 막혔다"
        tracker.record_sent(32_000)
        clock.advance(1)
    assert tracker.precheck(32_000).kind is VerdictKind.WAIT


# --- 모델 추천 (계획서 2.3절) ------------------------------------------------------


def test_추천은_기본_모델을_우선한다(clock: FakeClock):
    book = QuotaBook(clock)
    rec = book.recommend(1_000, exclude=GEMMA)
    assert rec.model_id == GEMINI
    assert rec.available_now is True
    assert "남음" in rec.message


def test_추천은_RPD_여유가_없는_모델을_제외한다(clock: FakeClock):
    book = QuotaBook(clock)
    for _ in range(450):
        book.tracker(GEMINI).record_sent(10)
    rec = book.recommend(1_000, exclude=GEMMA)
    assert rec.model_id == GEMMA26  # Gemini 는 오늘 소진


def test_추천은_요청_크기가_맞는_모델만_고른다(clock: FakeClock):
    """20,000 토큰은 Gemma 의 요청당 한도를 넘는다."""
    book = QuotaBook(clock)
    rec = book.recommend(20_000, exclude=GEMINI)
    assert rec.model_id is None
    assert "모든 모델" in rec.message


def test_모두_대기중이면_가장_빨리_풀리는_모델을_안내한다(clock: FakeClock):
    book = QuotaBook(clock)
    book.tracker(GEMMA).record_sent(14_000)
    clock.advance(50)  # Gemma 는 10초 뒤 풀린다
    book.tracker(GEMMA26).record_sent(14_000)  # 26B 는 60초 뒤

    rec = book.recommend(1_000, exclude=GEMINI)
    assert rec.model_id == GEMMA
    assert rec.wait_s == pytest.approx(10.0)
    assert rec.available_now is False


def test_모든_모델의_일일_한도가_소진되면_안내한다(clock: FakeClock):
    book = QuotaBook(clock)
    for model_id, count in ((GEMINI, 450), (GEMMA, 12_960), (GEMMA26, 12_960)):
        for _ in range(count):
            book.tracker(model_id).record_sent(1)
    rec = book.recommend(100)
    assert rec.model_id is None
    assert "내일" in rec.message


# --- 게이지 -----------------------------------------------------------------------


def test_게이지는_창_안의_값만_보여준다(clock: FakeClock):
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(1_000)
    clock.advance(30)
    tracker.record_sent(2_000)

    gauges = tracker.gauges()
    assert gauges.requests_in_window == 2
    assert gauges.input_tokens_in_window == 3_000
    assert (gauges.rpm_limit, gauges.tpm_limit, gauges.rpd_limit) == (30, 16_000, 14_400)

    clock.advance(31)  # 첫 기록이 창을 벗어난다
    gauges = tracker.gauges()
    assert gauges.requests_in_window == 1
    assert gauges.input_tokens_in_window == 2_000
    assert gauges.daily_requests == 2  # 일일 카운터는 창과 무관하다


def test_대기_예고는_계산값이다(clock: FakeClock):
    """계획서 2.3절 — 추정이 아니라 계산으로 낼 것."""
    tracker = QuotaTracker(GEMMA, clock)
    tracker.record_sent(14_400)
    clock.advance(25)
    assert tracker.next_wait_s(1_000) == pytest.approx(35.0)
    clock.advance(35)
    assert tracker.next_wait_s(1_000) == 0
