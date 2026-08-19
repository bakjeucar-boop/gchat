"""세션 2 실측 스크립트 (계획서 4절 세션 2, 부록 B).

문서로만 알던 값을 실호출로 확인한다. 결과는 docs/api_findings.md 에 정리하고
확정된 값만 gchat/models.py 에 반영한다.

사용법:
    python scripts/probe_models.py sdk         # SDK 표면 확인 (API 호출 없음)
    python scripts/probe_models.py list        # 사용 가능한 모델 목록
    python scripts/probe_models.py specs       # 3개 모델 상세 (부록 B-5)
    python scripts/probe_models.py tokens      # count_tokens 동작 (부록 B-7)
    python scripts/probe_models.py stream      # 스트리밍 동작 (부록 B-6)
    python scripts/probe_models.py thinking    # thinking_level 수용값 (부록 B-4)
    python scripts/probe_models.py turns       # 한국어 턴당 토큰 (부록 B-3)
    python scripts/probe_models.py tpm         # TPM 산정 기준 (부록 B-1,2,8)
    python scripts/probe_models.py grounding   # 검색 그라운딩 (2.10절, 부록 B-11~13)
    python scripts/probe_models.py modes       # 응답 모드별 토큰·시간·잘림 (기술서 7.3절)

비용 주의: thinking / stream / grounding 은 짧은 프롬프트와 작은 max_output_tokens
로 제한한다. turns 와 tpm 만 의도적으로 토큰을 쓴다.
"""

from __future__ import annotations

import json
import os
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".streamlit" / "secrets.toml"

# 계획서 1.1절의 모델 ID
PLAN_MODELS = ("gemini-3.5-flash-lite", "gemma-4-31b-it", "gemma-4-26b-a4b-it")

SHORT_PROMPT = "1+1은?"
SHORT_MAX_OUT = 50


def load_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    if SECRETS.exists():
        data = tomllib.loads(SECRETS.read_text(encoding="utf-8"))
        key = data.get("GEMINI_API_KEY")
        if key:
            return str(key)
    raise SystemExit(
        "GEMINI_API_KEY 를 찾을 수 없습니다. .streamlit/secrets.toml 또는 환경변수를 확인하세요."
    )


def client() -> genai.Client:
    return genai.Client(api_key=load_api_key())


def err(exc: Exception) -> dict[str, Any]:
    """예외를 기록 가능한 형태로 편다. 스택트레이스 대신 구조를 본다."""
    out: dict[str, Any] = {"type": type(exc).__name__, "message": str(exc)[:600]}
    for attr in ("code", "status", "details", "response_json"):
        value = getattr(exc, attr, None)
        if value is not None:
            out[attr] = value if isinstance(value, (int, str)) else repr(value)[:800]
    return out


def usage(resp: Any) -> dict[str, Any]:
    """usage_metadata 를 통째로 딕셔너리로. 어떤 필드가 실제로 채워지는지 본다."""
    meta = getattr(resp, "usage_metadata", None)
    if meta is None:
        return {}
    out = {}
    for field in (
        "prompt_token_count",
        "candidates_token_count",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
        "cached_content_token_count",
        "total_token_count",
    ):
        out[field] = getattr(meta, field, None)
    return out


def thinking_config(level: str) -> types.ThinkingConfig:
    """SDK 가 thinking_level 을 지원하면 그것을, 아니면 budget 으로 낮춘다."""
    fields = set(types.ThinkingConfig.model_fields)
    if "thinking_level" in fields:
        return types.ThinkingConfig(thinking_level=level)
    raise SystemExit(f"이 SDK 의 ThinkingConfig 에 thinking_level 이 없습니다: {sorted(fields)}")


# --- 명령 -------------------------------------------------------------------


def cmd_sdk() -> dict[str, Any]:
    """API 호출 없이 SDK 표면만 본다."""
    import google.genai as g

    return {
        "google_genai_version": getattr(g, "__version__", "unknown"),
        "ThinkingConfig_fields": sorted(types.ThinkingConfig.model_fields),
        "GenerateContentConfig_fields": sorted(types.GenerateContentConfig.model_fields),
        "GoogleSearch_fields": sorted(types.GoogleSearch.model_fields),
        "GroundingMetadata_fields": sorted(types.GroundingMetadata.model_fields),
    }


def cmd_list() -> dict[str, Any]:
    # 페이저를 도는 동안 Client 가 살아 있어야 한다 (임시 객체면 중간에 닫힌다).
    cli = client()
    out = []
    for model in cli.models.list():
        out.append(
            {
                "name": model.name,
                "display_name": model.display_name,
                "input_token_limit": model.input_token_limit,
                "output_token_limit": model.output_token_limit,
                "supported_actions": list(model.supported_actions or []),
            }
        )
    return {"count": len(out), "models": out}


def cmd_specs() -> dict[str, Any]:
    """부록 B-5 — 최대 출력 토큰과 컨텍스트 윈도우를 서버에서 직접 받는다."""
    cli = client()
    out = {}
    for model_id in PLAN_MODELS:
        try:
            model = cli.models.get(model=model_id)
            out[model_id] = {
                "name": model.name,
                "display_name": model.display_name,
                "version": model.version,
                "input_token_limit": model.input_token_limit,
                "output_token_limit": model.output_token_limit,
                "supported_actions": list(model.supported_actions or []),
            }
        except Exception as exc:  # noqa: BLE001 — 구조를 기록하는 것이 목적
            out[model_id] = {"error": err(exc)}
    return out


def cmd_tokens() -> dict[str, Any]:
    """부록 B-7 — count_tokens 가 Gemma 에서도 동작하는가."""
    cli = client()
    texts = {
        "korean": "인버터 용량을 어떻게 정하나요? 태양광 발전소 설계 기준을 알려주세요.",
        "english": "How do I size an inverter for a solar plant? Explain the DC/AC ratio.",
    }
    out: dict[str, Any] = {}
    for model_id in PLAN_MODELS:
        entry: dict[str, Any] = {}
        for name, text in texts.items():
            try:
                resp = cli.models.count_tokens(model=model_id, contents=text)
                entry[name] = {
                    "chars": len(text),
                    "total_tokens": resp.total_tokens,
                    "chars_per_token": round(len(text) / resp.total_tokens, 2)
                    if resp.total_tokens
                    else None,
                }
            except Exception as exc:  # noqa: BLE001
                entry[name] = {"error": err(exc)}
        out[model_id] = entry
    return out


def cmd_stream() -> dict[str, Any]:
    """부록 B-6 — 세 모델 모두 generate_content_stream 이 동작하는가."""
    cli = client()
    out = {}
    for model_id in PLAN_MODELS:
        try:
            started = time.monotonic()
            chunks, text, last = 0, [], None
            for chunk in cli.models.generate_content_stream(
                model=model_id,
                contents=SHORT_PROMPT,
                config=types.GenerateContentConfig(max_output_tokens=SHORT_MAX_OUT),
            ):
                chunks += 1
                if chunk.text:
                    text.append(chunk.text)
                last = chunk
            out[model_id] = {
                "ok": True,
                "chunks": chunks,
                "elapsed_s": round(time.monotonic() - started, 2),
                "text": "".join(text)[:200],
                "usage_on_last_chunk": usage(last),
                "finish_reason": str(last.candidates[0].finish_reason)
                if last and last.candidates
                else None,
            }
        except Exception as exc:  # noqa: BLE001
            out[model_id] = {"ok": False, "error": err(exc)}
    return out


def cmd_thinking() -> dict[str, Any]:
    """부록 B-4 — 계획서 1.2절 표의 근거. Gemma 에 medium 을 보내면 정말 400인가."""
    cli = client()
    out: dict[str, Any] = {}
    for model_id in PLAN_MODELS:
        entry: dict[str, Any] = {}
        for level in ("minimal", "medium", "high"):
            try:
                resp = cli.models.generate_content(
                    model=model_id,
                    contents=SHORT_PROMPT,
                    config=types.GenerateContentConfig(
                        max_output_tokens=SHORT_MAX_OUT,
                        thinking_config=thinking_config(level),
                    ),
                )
                entry[level] = {
                    "accepted": True,
                    "text": (resp.text or "")[:80],
                    "usage": usage(resp),
                    "finish_reason": str(resp.candidates[0].finish_reason)
                    if resp.candidates
                    else None,
                }
            except Exception as exc:  # noqa: BLE001
                entry[level] = {"accepted": False, "error": err(exc)}
            time.sleep(1)
        out[model_id] = entry
    return out


# 부록 B-3 — 한국어 대화 5턴. 1.5절 표의 "턴당 약 600토큰" 가정을 검증한다.
KOREAN_TURNS = [
    "태양광 발전소에 쓰는 인버터 용량은 어떻게 정하나요? 간단히 설명해 주세요.",
    "DC/AC 비율을 1.2로 잡으면 어떤 손실이 생기나요?",
    "그러면 우리나라 기후에서는 몇 대 몇 정도가 적당한가요?",
    "앞에서 말한 손실을 줄이려면 설계에서 무엇을 바꿔야 하나요?",
    "지금까지 이야기한 내용을 세 줄로 정리해 주세요.",
]


def cmd_turns() -> dict[str, Any]:
    """실제 대화 형태로 턴당 누적 토큰 증가량을 잰다.

    Gemma 예산 3,000 이 몇 턴에 해당하는지가 이 측정에 달려 있다.
    Gemma 는 TPM 16,000 이 좁아 5턴을 연속으로 돌리면 스스로 429 를 맞으므로
    턴당 토큰량 측정은 Gemini 로 하고, 같은 이력을 Gemma 로 count_tokens 만 한다.
    """
    cli = client()
    model_id = "gemini-3.5-flash-lite"
    history: list[types.Content] = []
    rows = []
    for i, text in enumerate(KOREAN_TURNS, start=1):
        history.append(types.Content(role="user", parts=[types.Part(text=text)]))
        try:
            resp = cli.models.generate_content(
                model=model_id,
                contents=history,
                config=types.GenerateContentConfig(
                    max_output_tokens=512,
                    thinking_config=thinking_config("minimal"),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            rows.append({"turn": i, "error": err(exc)})
            break
        answer = resp.text or ""
        history.append(types.Content(role="model", parts=[types.Part(text=answer)]))
        row: dict[str, Any] = {
            "turn": i,
            "user_chars": len(text),
            "model_chars": len(answer),
            "usage": usage(resp),
            "finish_reason": str(resp.candidates[0].finish_reason) if resp.candidates else None,
        }
        # 같은 이력을 Gemma 로 세면 예산 3,000 대비 위치를 알 수 있다.
        for gemma in ("gemma-4-31b-it", "gemma-4-26b-a4b-it"):
            try:
                row[f"count_tokens[{gemma}]"] = cli.models.count_tokens(
                    model=gemma, contents=history
                ).total_tokens
            except Exception as exc:  # noqa: BLE001
                row[f"count_tokens[{gemma}]"] = err(exc)
        rows.append(row)
        time.sleep(2)
    return {"model": model_id, "turns": rows}


def log(message: str) -> None:
    """진행 상황을 즉시 stderr 로 흘린다. 긴 실험을 눈으로 좇기 위한 것."""
    print(message, file=sys.stderr, flush=True)


def cmd_tpm() -> dict[str, Any]:
    """부록 B-1,2,8 — TPM 이 입력만 세는가, 입출력을 함께 세는가.

    Gemma(TPM 16,000)를 대상으로 두 방향에서 한도를 때린다.

    1단계 (입력 쪽): 큰 입력(약 4,000토큰) + 작은 출력(64).
       입력을 센다면 4~5회에서 429. 요청 자체가 빨라 몇십 초면 끝난다.
    2단계 (출력 쪽): 작은 입력(약 30토큰) + 큰 출력(2,048).
       출력을 센다면 8회 안팎에서 429. 세지 않는다면 RPM 30 까지 안 걸린다.
    3단계 (사고 쪽): 2단계와 같되 thinking high. 429 시점이 당겨지면
       사고 토큰도 산입되는 것이다.

    각 단계 사이에 60초 슬라이딩 윈도우가 비도록 기다린다.
    """
    cli = client()
    model_id = "gemma-4-31b-it"

    # 약 4,000 토큰짜리 입력을 만들고 실제 토큰 수를 확인한다.
    unit = "태양광 발전소의 인버터 용량 산정은 직류 대 교류 비율과 일사량 분포에 따라 달라진다. "
    filler = unit * 300
    filler_tokens = cli.models.count_tokens(model=model_id, contents=filler).total_tokens
    log(f"[준비] filler {len(filler):,}자 = {filler_tokens:,}토큰")

    def burst(
        label: str,
        prompt: str,
        level: str,
        max_out: int,
        limit: int,
    ) -> dict[str, Any]:
        cfg = types.GenerateContentConfig(
            max_output_tokens=max_out,
            # Gemma 는 thinking_config 를 생략하면 사고가 켜진 채 동작한다 (stream 실측).
            thinking_config=thinking_config(level),
        )
        rows: list[dict[str, Any]] = []
        cumulative_total = cumulative_prompt = 0
        blocked = None
        started = time.monotonic()
        for i in range(1, limit + 1):
            try:
                resp = cli.models.generate_content(model=model_id, contents=prompt, config=cfg)
                u = usage(resp)
                cumulative_total += u.get("total_token_count") or 0
                cumulative_prompt += u.get("prompt_token_count") or 0
                at = round(time.monotonic() - started, 1)
                rows.append(
                    {
                        "n": i,
                        "at_s": at,
                        "usage": u,
                        "cumulative_prompt": cumulative_prompt,
                        "cumulative_total": cumulative_total,
                    }
                )
                log(
                    f"[{label}] {i}/{limit} at {at}s  "
                    f"in={u.get('prompt_token_count')} out={u.get('candidates_token_count')} "
                    f"think={u.get('thoughts_token_count')} total={u.get('total_token_count')} "
                    f"| 누적 입력 {cumulative_prompt:,} / 누적 전체 {cumulative_total:,}"
                )
            except Exception as exc:  # noqa: BLE001
                at = round(time.monotonic() - started, 1)
                e = err(exc)
                blocked = {
                    "n": i,
                    "at_s": at,
                    "cumulative_prompt_before": cumulative_prompt,
                    "cumulative_total_before": cumulative_total,
                    "error": e,
                }
                log(
                    f"[{label}] {i}번째에서 차단 at {at}s :: {e.get('code')} {e.get('status')} "
                    f"| 직전 누적 입력 {cumulative_prompt:,} / 전체 {cumulative_total:,}"
                )
                break
        return {
            "label": label,
            "thinking_level": level,
            "max_output_tokens": max_out,
            "rows": rows,
            "blocked": blocked,
        }

    def parallel_burst(label: str, level: str, n: int, max_out: int) -> dict[str, Any]:
        """긴 출력을 병렬로 보내 60초 창 안에 토큰을 몰아넣는다.

        Gemma 의 긴 응답은 1회에 45초가 걸려 순차 전송으로는 창이 차기 전에
        비어 버린다. 입력은 작게(약 16토큰) 두므로, 창을 채우는 것은 오직 출력이다.
        버스트 직후 아주 작은 요청 하나를 더 보내 429 여부를 본다.
          - 429 → 출력(및 사고) 토큰이 TPM 에 산입된다
          - 정상 → 입력만 센다 (입력 누계는 수백 토큰에 불과하므로)
        """
        prompt = "한국의 사계절을 아주 길고 자세하게 설명해 주세요."
        cfg = types.GenerateContentConfig(
            max_output_tokens=max_out,
            thinking_config=thinking_config(level),
        )

        def one(i: int) -> dict[str, Any]:
            try:
                resp = cli.models.generate_content(model=model_id, contents=prompt, config=cfg)
                return {"n": i, "ok": True, "usage": usage(resp)}
            except Exception as exc:  # noqa: BLE001
                return {"n": i, "ok": False, "error": err(exc)}

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=n) as pool:
            rows = list(pool.map(one, range(1, n + 1)))
        elapsed = round(time.monotonic() - started, 1)

        ok_rows = [r for r in rows if r["ok"]]
        totals = {
            "prompt": sum(r["usage"].get("prompt_token_count") or 0 for r in ok_rows),
            "candidates": sum(r["usage"].get("candidates_token_count") or 0 for r in ok_rows),
            "thoughts": sum(r["usage"].get("thoughts_token_count") or 0 for r in ok_rows),
            "total": sum(r["usage"].get("total_token_count") or 0 for r in ok_rows),
        }
        log(
            f"[{label}] {len(ok_rows)}/{n} 성공, {elapsed}s | "
            f"입력 {totals['prompt']:,} · 본문 {totals['candidates']:,} · "
            f"사고 {totals['thoughts']:,} · 전체 {totals['total']:,}"
        )

        # 버스트 직후의 작은 확인 요청. 이것이 판별식이다.
        try:
            probe = cli.models.generate_content(
                model=model_id,
                contents=SHORT_PROMPT,
                config=types.GenerateContentConfig(
                    max_output_tokens=32, thinking_config=thinking_config("minimal")
                ),
            )
            after = {"blocked": False, "usage": usage(probe)}
            log(f"[{label}] 직후 확인 요청: 통과 → 창에 출력 토큰이 쌓이지 않았다")
        except Exception as exc:  # noqa: BLE001
            e = err(exc)
            after = {"blocked": True, "error": e}
            log(f"[{label}] 직후 확인 요청: 차단 {e.get('code')} → 창이 찼다")

        return {
            "label": label,
            "thinking_level": level,
            "requests": n,
            "max_output_tokens": max_out,
            "elapsed_s": elapsed,
            "totals": totals,
            "rows": rows,
            "probe_after_burst": after,
        }

    out: dict[str, Any] = {"model": model_id, "filler_tokens": filler_tokens}

    out["phase1_big_input"] = burst(
        "1단계 큰입력", filler + "\n한 문장으로 요약해 주세요.", "minimal", 64, 6
    )

    log("[대기] 60초 윈도우가 비도록 70초 쉼")
    time.sleep(70)
    # 입력은 12회 합쳐도 200토큰 남짓. 출력이 산입된다면 2만 토큰 가까이 쌓인다.
    out["phase2_big_output_parallel"] = parallel_burst("2단계 병렬 큰출력", "minimal", 12, 2048)

    log("[대기] 60초 윈도우가 비도록 70초 쉼")
    time.sleep(70)
    # 사고 켬. 본문만 세는지 사고까지 세는지는 totals 의 비율을 보고 판정한다.
    out["phase3_thinking_parallel"] = parallel_burst("3단계 병렬 사고켬", "high", 8, 2048)
    return out


GROUNDING_QUESTION = "오늘 서울의 날씨는?"
PLAIN_QUESTION = "1+1은?"


def _grounding_dump(resp: Any) -> dict[str, Any]:
    if not resp.candidates:
        return {"candidates": 0}
    cand = resp.candidates[0]
    meta = getattr(cand, "grounding_metadata", None)
    if meta is None:
        return {"grounding_metadata": None, "attr_present": hasattr(cand, "grounding_metadata")}
    chunks = getattr(meta, "grounding_chunks", None) or []
    supports = getattr(meta, "grounding_supports", None) or []
    entry = getattr(meta, "search_entry_point", None)
    rendered = getattr(entry, "rendered_content", None) if entry else None
    return {
        "grounding_metadata": "present",
        "web_search_queries": list(getattr(meta, "web_search_queries", None) or []),
        "grounding_chunks_count": len(chunks),
        "grounding_chunks_sample": [
            {
                "title": getattr(getattr(c, "web", None), "title", None),
                "uri": (getattr(getattr(c, "web", None), "uri", "") or "")[:120],
                "domain": getattr(getattr(c, "web", None), "domain", None),
            }
            for c in chunks[:3]
        ],
        "grounding_supports_count": len(supports),
        "search_entry_point_present": entry is not None,
        "rendered_content_len": len(rendered) if rendered else 0,
        "rendered_content_head": (rendered or "")[:160],
    }


def cmd_grounding() -> dict[str, Any]:
    """계획서 2.10절 + 부록 B-11~13."""
    cli = client()
    tool = types.Tool(google_search=types.GoogleSearch())
    out: dict[str, Any] = {}

    cases = [
        ("gemini_search_on__needs_search", "gemini-3.5-flash-lite", GROUNDING_QUESTION, True),
        ("gemini_search_on__plain", "gemini-3.5-flash-lite", PLAIN_QUESTION, True),
        ("gemini_search_off__plain", "gemini-3.5-flash-lite", PLAIN_QUESTION, False),
        ("gemma_search_on", "gemma-4-31b-it", GROUNDING_QUESTION, True),
    ]
    for name, model_id, question, with_tool in cases:
        cfg: dict[str, Any] = {"max_output_tokens": 100}
        if with_tool:
            cfg["tools"] = [tool]
        try:
            resp = cli.models.generate_content(
                model=model_id, contents=question, config=types.GenerateContentConfig(**cfg)
            )
            out[name] = {
                "ok": True,
                "model": model_id,
                "text": (resp.text or "")[:160],
                "usage": usage(resp),
                "grounding": _grounding_dump(resp),
            }
        except Exception as exc:  # noqa: BLE001
            out[name] = {"ok": False, "model": model_id, "error": err(exc)}
        time.sleep(1)
    return out


# --- 응답 모드 (기술서 7.3절) --------------------------------------------------
#
# 세션 7 측정을 재현한다. 이 측정은 임시 스크립트로 한 번 재고 버렸다가
# 기술서 7장을 쓰면서 여기로 옮겼다 — 모델이 바뀌면 다시 재야 하는 값이다.
#
# 짧은 프롬프트(1+1)로는 사고가 거의 일어나지 않아 모드 차이가 드러나지 않는다.
# 설명을 요구하는 질문이어야 한다.
MODE_PROMPT = "태양광 인버터의 DC/AC 비율을 정하는 기준을 설명해 주세요."

# max_output 은 앱의 모델별 기본값(models.py default_max_output)과 같게 둔다.
# Gemma high 의 잘림은 이 상한과 사고 토큰의 관계에서 나오므로 값을 바꾸면
# 재현되지 않는다.
MODE_CASES = (
    ("gemini-3.5-flash-lite", "minimal", 4_096),
    ("gemini-3.5-flash-lite", "medium", 4_096),
    ("gemini-3.5-flash-lite", "high", 4_096),
    ("gemma-4-31b-it", "minimal", 2_048),
    ("gemma-4-31b-it", "high", 2_048),
)


def cmd_modes() -> dict[str, Any]:
    """응답 모드가 한도·응답 시간·잘림에 주는 영향을 잰다.

    확인할 것은 세 가지다.
    - **입력 토큰이 모드와 무관하게 같은가.** 같다면 응답 모드는 TPM 에
      영향을 주지 않는다 (TPM 은 입력만 센다 — cmd_tpm 참조)
    - 응답 시간이 얼마나 늘어나는가. 모드를 올리는 대가는 이것뿐이어야 한다
    - 사고 토큰이 max_output 을 잠식해 답변이 잘리는가. Gemma 는 상한이
      좁아 사고를 켜면 잘린다

    비용 주의: 5회 호출이고 출력을 상한까지 쓸 수 있다. tpm/turns 다음으로 무겁다.
    """
    cli = client()
    out: dict[str, Any] = {}
    for model_id, level, max_out in MODE_CASES:
        key = f"{model_id}:{level}"
        started = time.monotonic()
        try:
            resp = cli.models.generate_content(
                model=model_id,
                contents=MODE_PROMPT,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_out,
                    thinking_config=thinking_config(level),
                ),
            )
            elapsed = time.monotonic() - started
            finish = str(resp.candidates[0].finish_reason) if resp.candidates else None
            counts = usage(resp)
            out[key] = {
                "ok": True,
                "max_output_tokens": max_out,
                "elapsed_s": round(elapsed, 1),
                "input_tokens": counts.get("prompt_token_count"),
                "thoughts_tokens": counts.get("thoughts_token_count") or 0,
                "output_tokens": counts.get("candidates_token_count") or 0,
                "finish_reason": finish,
                "truncated": bool(finish and finish.endswith("MAX_TOKENS")),
                "text_head": (resp.text or "")[:80],
            }
        except Exception as exc:  # noqa: BLE001
            out[key] = {"ok": False, "max_output_tokens": max_out, "error": err(exc)}
        time.sleep(2)

    # 입력 토큰이 모드와 무관하다는 것이 이 측정의 핵심이므로 따로 뽑아 둔다.
    by_model: dict[str, list[Any]] = {}
    for model_id, level, _ in MODE_CASES:
        entry = out[f"{model_id}:{level}"]
        if entry.get("ok"):
            by_model.setdefault(model_id, []).append(entry["input_tokens"])
    out["_input_tokens_identical_per_model"] = {
        model_id: len(set(values)) == 1 for model_id, values in by_model.items()
    }
    return out


COMMANDS = {
    "sdk": cmd_sdk,
    "list": cmd_list,
    "specs": cmd_specs,
    "tokens": cmd_tokens,
    "stream": cmd_stream,
    "thinking": cmd_thinking,
    "turns": cmd_turns,
    "tpm": cmd_tpm,
    "grounding": cmd_grounding,
    "modes": cmd_modes,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        print("사용 가능한 명령:", ", ".join(COMMANDS))
        return 1
    name = argv[1]
    result = COMMANDS[name]()
    text = json.dumps({name: result}, ensure_ascii=False, indent=2, default=str)
    print(text)
    if len(argv) > 2:
        Path(argv[2]).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
