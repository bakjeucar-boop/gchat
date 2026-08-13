# gchat

Streamlit 기반 개인용 AI 챗봇. Google Gemini API (google-genai SDK) 사용.

요구사항과 개발 계획은 [doc/gchat_계획서.md](doc/gchat_계획서.md)에 있다.

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

## 상태

세션 1 (골격 · 모델 테이블 · 인증 게이트) 완료. 응답 생성은 세션 2부터.
