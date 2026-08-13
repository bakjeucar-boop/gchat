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

## 상태

세션 2 완료 — 골격 · 모델 테이블(실측 반영) · 인증 게이트 · Gemini 래퍼(`client.py`).
채팅 화면은 세션 4부터.
