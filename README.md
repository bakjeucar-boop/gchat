# gchat

Streamlit 기반 **개인용 AI 챗봇**. Google Gemini API (google-genai SDK) 사용.

무료 티어 한도 안에서 혼자 쓰려고 만든 앱이다. 무료 한도는 좁고 요청마다
방식이 다르게 걸리므로, 이 앱의 절반은 **한도를 미리 재고 넘지 않게 막는 일**이다.

앱: <https://rvxu3cqzajtbassupttgph.streamlit.app/> (비밀번호로 잠겨 있다)

## 무엇을 하는가

- **모델 3개** — Gemini 3.5 Flash-Lite(기본), Gemma 4 31B, Gemma 4 26B A4B.
  Gemini 의 하루 한도가 떨어졌을 때 Gemma 로 넘어가는 구성이다
- **응답은 항상 스트리밍**하고, 길어지면 ⏹ 멈춤으로 끊을 수 있다.
  그때까지 받은 답은 남고 "계속"이라고 입력하면 이어 쓴다
- **한도를 세 가지로 실시간 표시** — 분당 사용량 / 분당 요청 / 일일 요청.
  보내기 전에 넘을 것 같으면 막고, 얼마나 기다려야 하는지 알려준다
- **용도 프리셋** — 범용 / 코딩 / 커스텀. 코딩일 때만 출력 상한을 올린다
- **대화는 이 세션에만 존재한다.** 새로고침하면 사라진다. 남기려면
  Markdown 으로 내려받는다 (대화 하나 또는 전체)
- 비밀번호 한 개로 접근을 막는다. 암호화가 아니라 "문 잠금"이고,
  목적은 API 키 남용으로 인한 과금 방지다

**의도적으로 없는 것**: 대화 영구 저장(DB·파일), 파일 첨부, 웹 검색 그라운딩
(무료 티어에서 API 자체가 막혀 있다), Google 로그인, 컨텍스트 요약 압축.

## 직접 돌려보려면

필요한 것은 **Google AI Studio 의 API 키**(무료) 하나뿐이다.
<https://aistudio.google.com/apikey> 에서 발급한다.

```
git clone https://github.com/bakjeucar-boop/gchat.git
cd gchat
pip install -r requirements.txt
```

`.streamlit/secrets.toml.example` 을 `.streamlit/secrets.toml` 로 복사하고
값을 채운다. **이 파일은 커밋하지 않는다** (`.gitignore` 에 있다).

```toml
GEMINI_API_KEY = "AI Studio 에서 발급한 키"
APP_PASSWORD = "20자 이상 무작위 문자열"
```

```
streamlit run app.py
```

개발까지 하려면 `pip install -r requirements-dev.txt` (pytest·ruff 포함).

### 검사

```
pytest
ruff check .
```

### 모델 실측

모델별 한도·파라미터는 문서가 서로 엇갈려서 실호출로 확인했다.
결과는 [docs/api_findings.md](docs/api_findings.md) 에 있다.

```
python scripts/probe_models.py sdk
```

## 배포 (Streamlit Community Cloud)

1. GitHub 저장소에 push (`.streamlit/secrets.toml` 은 커밋되지 않는다)
2. <https://share.streamlit.io> 에서 앱 생성
   - Main file path: `app.py`
   - Advanced settings → **Python version 3.12**
3. 같은 Advanced settings 의 **Secrets** 에 등록한다 (TOML 이라 따옴표가 필요하다)

```toml
GEMINI_API_KEY = "..."
APP_PASSWORD = "..."
```

알아둘 것:

- 무료 계정은 **비공개 앱 1개, 공개 앱 무제한**이다. 이 저장소가 공개인 이유이며,
  접근은 비밀번호 게이트가 막는다. secrets 는 저장소에 없으므로 키는 안전하다
- **12시간 무접속이면 앱이 대기 상태로 내려가고, 깨어나면 대화가 모두 사라진다.**
  고장이 아니라 대화를 세션에만 두기로 한 설계의 결과다 (계획서 6절)
- 서버는 UTC 로 돌지만 화면과 내보내기의 시각은 KST 로 변환한다
  (`requirements.txt` 의 `tzdata` 가 이 때문에 필요하다)

## 구조

의존 방향은 한쪽으로만 흐른다.

```
models → state → context → quota → client → export → ui → app
```

| 파일 | 역할 |
|---|---|
| `gchat/models.py` | 모델별 능력·한도 **단일 테이블**. 다른 곳은 값을 하드코딩하지 않는다 |
| `gchat/state.py` | 대화·설정·session_state |
| `gchat/context.py` | 토큰 추정과 예산 절단 |
| `gchat/quota.py` | 60초 창 한도 추적, 서버 429 로 보정 |
| `gchat/client.py` | google-genai 호출, 오류를 6종으로 번역 |
| `gchat/export.py` | Markdown 내보내기 |
| `gchat/ui/` | 화면 조각. streamlit 위젯을 직접 다루는 유일한 계층 |

요구사항·설계 근거·실측 기록은 모두
[docs/gchat_계획서.md](docs/gchat_계획서.md) 에 있다. 코드와 문서가 어긋나면
계획서가 기준이다.

## 상태

**세션 8 완료 — 배포까지 끝났다.** 대화·한도 관리·컨트롤·Markdown 내보내기·
용도 프리셋·멈춤 버튼이 모두 동작하고, Streamlit Community Cloud 에서 돌고 있다.
