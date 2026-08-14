# gchat

Streamlit 기반 개인용 AI 챗봇. Google Gemini API (google-genai SDK) 사용.

요구사항과 개발 계획은 [docs/gchat_계획서.md](docs/gchat_계획서.md)에 있다.

## 설치

```
pip install -r requirements-dev.txt
```

배포(Streamlit Community Cloud)에는 `requirements.txt`만 쓴다.

## 설정

`.streamlit/secrets.toml.example`을 `.streamlit/secrets.toml`로 복사하고 값을 채운다.
이 파일은 절대 커밋하지 않는다.

```toml
GEMINI_API_KEY = "..."
APP_PASSWORD = "..."
```

## 실행

```
streamlit run app.py
```

## 검사

```
pytest
ruff check .
```

## 실측

`scripts/probe_models.py`로 모델 한도·파라미터를 실호출로 확인한다.
결과는 [docs/api_findings.md](docs/api_findings.md)에 있다.

```
python scripts/probe_models.py sdk
```

## 배포 (Streamlit Community Cloud)

1. GitHub 저장소에 push (`.streamlit/secrets.toml`은 커밋되지 않는다)
2. Streamlit Cloud 에서 앱 생성 — main 파일은 `app.py`
3. 앱 설정 화면의 Secrets 에 아래를 등록한다

```toml
GEMINI_API_KEY = "..."
APP_PASSWORD = "..."
```

비공개 앱 슬롯이 이미 차 있으면 저장소가 공개된다. secrets 는 저장소에 없으므로
키는 안전하다. 12시간 무접속 시 앱이 대기 상태가 되고, 깨어나면 대화가 모두
사라진다 — v1 의 설계 결과다 (계획서 6절).

## 상태

세션 6 완료 — 대화·한도 관리·컨트롤·Markdown 내보내기까지 동작한다.
