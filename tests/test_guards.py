"""원칙을 지키는 소스 검사 (기술서 8.8절 승급 작업).

여기 있는 테스트는 **동작이 아니라 소스를 본다.** 그래서 다른 테스트와 성격이 다르다.

    동작 테스트: 통과 = 그 동작이 맞다
    소스 검사:   통과 = 그 표식이 아직 거기 있다

**"통과했다"가 "안전하다"를 뜻하지 않는다.** 각 검사마다 무엇을 잡고 무엇을 못 잡는지
아래에 적었다. 그 구멍을 모르면 "테스트가 통과했으니 이 원칙은 지켜지고 있다"고
오해하게 되고, 그런 오해를 만드는 테스트는 없는 것보다 나쁘다
(기술서 8.3절 「버튼 라벨의 글자 크기가 안 줄어듦」이 그 부류였다).
"""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from gchat import models

ROOT = Path(__file__).resolve().parent.parent
CHAT = ROOT / "gchat" / "ui" / "chat.py"
CONTROLS = ROOT / "gchat" / "ui" / "controls.py"


# --- 배치 원칙 (기술서 9.3절 · 8.2절) ------------------------------------------------
#
# 즉시 반응해야 하는 UI 는 두 가지 방법 중 하나로 화면 안에 둔다.
#   1. position: fixed 로 화면에 고정 (멈춤 버튼)
#   2. 조작한 위젯 바로 옆에 그린다 (계열 전환 확인 창 → 사이드바 설정)
#
# **이 검사가 잡는 것** — 이미 적용된 두 장치가 사라지거나 서로 어긋나는 회귀.
#   고정 CSS 를 지우는 것, 컨테이너 키만 바꿔 CSS 선택자와 어긋나게 두는 것,
#   확인 창을 사이드바 설정 밖으로 옮기는 것.
#
# **이 검사가 못 잡는 것 (중요)**
#   - **새로 추가되는 UI 는 전혀 보지 못한다.** 아래 목록은 사람이 손으로 관리한다.
#     즉시 반응 UI 를 새로 만들면 이 파일에 검사도 함께 추가해야 한다
#   - 화면 좌표를 보지 않는다. CSS 가 있어도 다른 규칙에 덮여 실제로는 화면 밖일 수
#     있다. 좌표 판정은 사람이 브라우저에서 한다 (기술서 12.4절)
#   - 긴 대화에서만 재현되는 문제도 보지 못한다 (기술서 12.3절)


def test_멈춤_버튼은_화면에_고정된다():
    """스트리밍 중 눌러야 하는 버튼이라 본문 흐름에 두면 화면 밖으로 밀린다."""
    source = CHAT.read_text(encoding="utf-8")
    assert "position: fixed" in source, (
        "멈춤 버튼의 화면 고정이 사라졌다. 긴 대화에서 버튼이 화면 밖에 그려진다 (기술서 8.2절)"
    )


def test_멈춤_버튼의_CSS_선택자와_컨테이너_키가_같다():
    """키만 바꾸면 CSS 가 아무 곳도 가리키지 않게 된다 — 조용히 원칙이 깨진다."""
    source = CHAT.read_text(encoding="utf-8")
    keys = set(re.findall(r'st\.container\(key="([^"]+)"\)', source))
    selectors = set(re.findall(r"\.st-key-([A-Za-z0-9_]+)\s*\{", source))
    assert keys, "멈춤 버튼을 감싸는 st.container(key=...) 를 찾지 못했다"
    assert keys <= selectors, (
        f"컨테이너 키 {sorted(keys - selectors)} 를 가리키는 CSS 가 없다. "
        "키를 바꿨다면 CSS 선택자도 함께 바꿀 것"
    )


def test_계열_전환_확인은_모델_드롭다운_옆에서_그려진다():
    """본문 위쪽에 두면 긴 대화에서 화면 6,220px 위에 놓인다 (기술서 8.2절)."""
    source = CONTROLS.read_text(encoding="utf-8")
    settings = source[source.index("def render_settings") : source.index("def _render_model")]
    assert "render_family_confirmation" in settings, (
        "확인 창이 사이드바 설정 안에서 그려지지 않는다. 조작한 위젯 옆에 두는 것이 "
        "이 UI 의 배치 방법이다 (기술서 9.3절)"
    )
    assert settings.index("_render_model") < settings.index("render_family_confirmation"), (
        "확인 창은 모델 드롭다운 **바로 아래**여야 한다"
    )


# --- 인스트럭션 문구 (기술서 7.5절 · 12.5절) ----------------------------------------
#
# 답변 길이를 정하는 것은 출력 상한이 아니라 이 문구다. 문구를 고치면 길이가 바뀌는데,
# 그 변화는 실호출로만 확인된다 — 자동 검사가 불가능하다.
#
# 그래서 길이를 검사하는 대신 **재측정을 강제한다.** 문구가 바뀌면 이 테스트가 실패해
# 무엇을 해야 하는지 알린다.
#
# **이 검사가 잡는 것** — 문구를 고치고 재측정 없이 넘어가는 것.
# **이 검사가 못 잡는 것**
#   - 문구가 그대로여도 모델이 바뀌면 길이는 달라진다. 그건 이 검사 밖이다
#   - 해시만 갱신하고 측정을 건너뛰는 것은 막을 수 없다. 실패 메시지가 무엇을 해야
#     하는지 말해주는 것이 전부다

INSTRUCTION_HASH = "6d0ea6297c825431"

REMEASURE = (
    "인스트럭션 문구가 바뀌었습니다.\n"
    "기술서 7.5절 측정(같은 질문 3개로 답변 토큰 재측정)을 다시 하고,\n"
    "결과를 7.5절에 반영한 뒤 이 해시를 갱신하세요.\n"
    "문구만 고치고 넘어가면 길이가 목표 구간을 벗어나도 알 수 없습니다 "
    "(1차 개정 때 지시의 38%까지 떨어진 적이 있습니다).\n"
    "새 해시: {actual}"
)


def instruction_digest() -> str:
    """답변 길이에 영향을 주는 문구를 모아 해시한다.

    용도 프리셋 문구도 포함한다 — 최종 인스트럭션이 "용도 문구 + 길이 지시"라서
    어느 쪽이 바뀌어도 길이가 달라진다.
    """
    parts = [
        models.GEMMA_DEFAULT_INSTRUCTION,
        models.GEMMA_CODING_INSTRUCTION,
        *(models.PURPOSE_INSTRUCTIONS[key] for key in sorted(models.PURPOSE_INSTRUCTIONS)),
    ]
    joined = "\n---\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def test_인스트럭션_문구가_바뀌면_재측정을_요구한다():
    actual = instruction_digest()
    assert actual == INSTRUCTION_HASH, REMEASURE.format(actual=actual)


# --- 개발 용어 노출 (기술서 9.3절 · 8.4절) ------------------------------------------
#
# 화면 문구에 문서 절 번호·HTTP 상태 코드·원본 오류 JSON·내부 식별자를 쓰지 않는다.
#
# **무엇을 "화면 문자열"로 보는가** — "ui 가 만든 문자열"이 아니라 **화면에 도달하는
# 문자열**이다. 그래서 두 곳을 본다.
#   1. 위젯 호출에 넘긴 문자열 리터럴 (st.info(...), col.button(...) 등)
#   2. client.py 예외의 __str__ 반환값. 화면에 그대로 나오는 문구이며, 실제로
#      8.4절의 유출이 이 경로였다
#
# client.py 의 `message` 필드는 대상이 아니다. **거기에는 원본 API 텍스트가 일부러
# 담긴다** — 화면에는 안 나오고 "자세한 내용"을 펼쳐야 보인다. 검사하면 정상 코드가
# 걸린다.
#
# **이 검사가 잡는 것** — 리터럴로 적힌 개발 용어가 화면 문구에 섞여 들어가는 것.
#
# **이 검사가 못 잡는 것 (중요)**
#   - **f-string 안 변수의 값.** f"{exc}" 처럼 값이 실행 중에 정해지면 소스에는
#     없다. 8.4절의 원본 JSON 유출이 바로 이 형태였으므로, 이 검사는 그 사고를
#     다시 잡지 못한다. 문자열을 화면에 낼 때 변수의 출처를 사람이 봐야 한다
#   - 다른 모듈이 만들어 넘긴 문구
#   - 문구가 어색하거나 라벨과 단위가 안 맞는 것 (그건 사람이 읽어야 안다)

WIDGET_METHODS = frozenset(
    {
        "button",
        "download_button",
        "form_submit_button",
        "caption",
        "markdown",
        "write",
        "info",
        "warning",
        "error",
        "success",
        "toast",
        "expander",
        "popover",
        "subheader",
        "header",
        "title",
        "text",
        "chat_input",
        "text_input",
        "text_area",
        "selectbox",
        "radio",
        "slider",
        "checkbox",
        "progress",
        "metric",
    }
)

# 문자열을 담는 인자만 본다. key·language 같은 것은 화면에 안 나온다.
TEXT_KWARGS = frozenset({"help", "label", "text", "body", "placeholder"})

# HTTP 상태 코드는 정규식(4xx/5xx)이 아니라 **목록**으로 좁힌다.
# \b(4|5)\d\d\b 로 잡으면 "400자", "500명" 같은 정상 문구가 계속 걸린다.
HTTP_CODES = ("400", "401", "403", "404", "429", "500", "502", "503")

FORBIDDEN = [
    (re.compile(r"(계획서|기술서)\s*\d"), "문서 절 번호"),
    (re.compile(r"\b(" + "|".join(HTTP_CODES) + r")\b"), "HTTP 상태 코드"),
    (re.compile(r"RESOURCE_EXHAUSTED|INVALID_ARGUMENT|UNAVAILABLE|MAX_TOKENS"), "API 상태 문자열"),
    (re.compile(r"\{'error'|\"status\"\s*:|'error'\s*:"), "원본 오류 JSON"),
    (
        re.compile(
            r"thinking_level|max_output_tokens|prompt_token_count|candidates_token_count"
            r"|session_state|quotaId|retryDelay|finish_reason"
        ),
        "내부 식별자",
    ),
]

SCREEN_FILES = sorted((ROOT / "gchat" / "ui").glob("*.py")) + [ROOT / "app.py"]


def _literal_parts(node: ast.AST) -> list[str]:
    """문자열 리터럴과 f-string 의 **리터럴 부분**을 모은다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
    return []


def screen_strings() -> list[tuple[str, str]]:
    """(출처, 문자열) 목록. 화면에 도달하는 것만 모은다."""
    found: list[tuple[str, str]] = []

    for path in SCREEN_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in WIDGET_METHODS:
                continue
            for arg in node.args:
                found += [(path.name, s) for s in _literal_parts(arg)]
            for kw in node.keywords:
                if kw.arg in TEXT_KWARGS:
                    found += [(path.name, s) for s in _literal_parts(kw.value)]

    client = ROOT / "gchat" / "client.py"
    tree = ast.parse(client.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "__str__":
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Constant, ast.JoinedStr)):
                    found += [("client.py __str__", s) for s in _literal_parts(sub)]
    return found


def test_화면_문구에_개발_용어가_없다():
    hits = [
        f"{where}: {label} — {text.strip()[:60]!r}"
        for where, text in screen_strings()
        for pattern, label in FORBIDDEN
        if pattern.search(text)
    ]
    assert not hits, "화면 문구에 개발 용어가 섞였다 (기술서 9.3절):\n  " + "\n  ".join(hits)


def test_화면_문자열_수집이_비어_있지_않다():
    """수집이 0건이면 위 검사는 언제나 통과한다 — 그런 테스트는 없는 것보다 나쁘다."""
    strings = screen_strings()
    assert len(strings) > 40, f"화면 문자열을 {len(strings)}개만 찾았다. 수집 규칙을 확인할 것"
    assert any(where == "client.py __str__" for where, _ in strings), (
        "예외 __str__ 문구를 수집하지 못했다"
    )


# --- 위젯 키 사후 대입 (기술서 8.3절) ------------------------------------------------
#
# Streamlit 은 위젯이 그려진 뒤 그 키에 대입하면 예외를 낸다. 되돌리기가 필요한 조작은
# on_click / on_change 콜백에서 해야 한다 — 콜백은 다음 실행의 위젯 생성 **전에** 돈다.
#
# **무엇을 "위젯 키"로 보는가** — `key=` 로 넘긴 값만 본다. session_state 대입 전부를
# 보면 앱 자체 상태(S_AUTOSEND · S_PARTIAL 등)까지 걸린다. 그것들은 위젯이 아니라서
# 아무 때나 대입해도 된다.
#
# **검사 범위는 gchat/ 전체다.** ui 만 보면 대입이 state.py 에 있어 검사 대상이 0건이
# 되고, 그건 언제나 통과하는 테스트다 (기술서 8.3절 「잘못 고른 측정」과 같은 부류).
#
# **판정** — 위젯 키에 대입하는 함수는 다음 중 하나여야 한다.
#   1. on_click / on_change 로 등록된 콜백
#   2. 그 콜백에서 (간접적으로) 불리는 함수
#   3. 위젯이 만들어지기 전에 도는 초기화 함수 (아래 목록)
#
# **이 검사가 못 잡는 것**
#   - 콜백이 아닌 곳에서도 불리는 함수는 구분하지 못한다. 호출 그래프만 보고
#     실행 시점은 보지 않는다
#   - 콜백을 문자열이나 변수로 넘기면 등록을 추적하지 못한다
#   - **콜백 등록이 사라지는 것은 잡지 못한다.** on_change= 를 지워도 같은 함수에
#     다른 콜백으로 닿으면 대입 위치는 여전히 정상이라 통과한다 (변형 시험으로 확인).
#     등록 누락은 다른 종류의 결함이며 이 검사 밖이다
#   - 위젯 키를 f-string 으로 만들면 값을 알 수 없다 (그 키에 대입하는 코드도 없다)

# 위젯이 하나도 만들어지기 전에 도는 함수. 여기서는 대입해도 안전하다.
INIT_FUNCTIONS = frozenset({"init_session_state"})

GCHAT_FILES = sorted((ROOT / "gchat").rglob("*.py")) + [ROOT / "app.py"]


def _module_constants() -> dict[str, str]:
    """S_UI_MODEL 처럼 모듈 상수로 둔 키를 값으로 푼다."""
    out: dict[str, str] = {}
    for path in GCHAT_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.startswith("S_"):
                            out[target.id] = node.value.value
    return out


def _key_name(node: ast.AST, constants: dict[str, str]) -> str | None:
    """key= 나 첨자에 쓰인 식을 키 이름으로 푼다. f-string 은 알 수 없다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.Attribute):
        return constants.get(node.attr)
    return None


def widget_keys() -> set[str]:
    constants = _module_constants()
    keys: set[str] = set()
    for path in GCHAT_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "key":
                        if name := _key_name(kw.value, constants):
                            keys.add(name)
    return keys


def _functions_and_calls() -> tuple[dict[str, ast.FunctionDef], dict[str, set[str]]]:
    funcs: dict[str, ast.FunctionDef] = {}
    calls: dict[str, set[str]] = {}
    for path in GCHAT_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef):
                funcs[node.name] = node
                names = set()
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        if isinstance(sub.func, ast.Name):
                            names.add(sub.func.id)
                        elif isinstance(sub.func, ast.Attribute):
                            names.add(sub.func.attr)
                calls[node.name] = names
    return funcs, calls


def callback_safe_functions() -> set[str]:
    """콜백으로 등록된 함수와 거기서 뻗어 나가는 함수들."""
    registered: set[str] = set()
    for path in GCHAT_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in ("on_click", "on_change"):
                        if isinstance(kw.value, ast.Name):
                            registered.add(kw.value.id)
                        elif isinstance(kw.value, ast.Attribute):
                            registered.add(kw.value.attr)

    _, calls = _functions_and_calls()
    safe = set(registered)
    frontier = list(registered)
    while frontier:
        name = frontier.pop()
        for callee in calls.get(name, ()):
            if callee not in safe:
                safe.add(callee)
                frontier.append(callee)
    return safe


def widget_key_assignments() -> list[tuple[str, str, str]]:
    """(파일, 함수, 키) — 위젯 키에 대입하는 자리를 모두 찾는다."""
    constants = _module_constants()
    keys = widget_keys()
    found: list[tuple[str, str, str]] = []
    for path in GCHAT_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(func):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    value = target.value
                    is_state = isinstance(value, ast.Attribute) and value.attr == "session_state"
                    if not is_state:
                        continue
                    if (name := _key_name(target.slice, constants)) and name in keys:
                        found.append((path.name, func.name, name))
    return found


def test_위젯_키_대입은_콜백_안에서만_한다():
    safe = callback_safe_functions() | INIT_FUNCTIONS
    bad = [
        f"{file}:{func}() 가 위젯 키 {key!r} 에 대입한다"
        for file, func, key in widget_key_assignments()
        if func not in safe
    ]
    assert not bad, (
        "위젯이 그려진 뒤 그 키에 대입하면 StreamlitAPIException 이 난다. "
        "on_click / on_change 콜백으로 옮길 것 (기술서 8.3절):\n  " + "\n  ".join(bad)
    )


def test_위젯_키_검사가_실제로_무언가를_보고_있다():
    """대상이 0건이면 위 검사는 언제나 통과한다."""
    assert len(widget_keys()) >= 3, "위젯 키를 거의 찾지 못했다. 수집 규칙을 확인할 것"
    assert widget_key_assignments(), (
        "위젯 키에 대입하는 자리를 하나도 못 찾았다 — 검사가 헛돌고 있다"
    )
    assert callback_safe_functions(), "콜백 등록을 하나도 못 찾았다"
