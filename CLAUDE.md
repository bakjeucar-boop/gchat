# gchat

Streamlit 기반 개인용 AI 챗봇. Google Gemini API 사용.

## 원칙
- `docs/gchat_계획서.md`가 단일 진실 원천이다. 다르게 구현할 이유가 생기면
  임의로 바꾸지 말고 근거를 보고하고 승인을 받은 뒤 계획서를 갱신한다.
- 모델별 능력·한도는 `gchat/models.py`의 테이블에만 기술한다.
  UI·client·quota는 이 테이블을 참조할 뿐 값을 하드코딩하지 않는다.
- 응답은 항상 스트리밍한다.
- 오류를 조용히 삼키지 않는다. 무엇이 왜 실패했는지 사용자에게 보인다.
- 한도 추적기는 추정치이고 서버 429가 최종 진실이다. 둘이 어긋나는 경우를
  정상 동작으로 간주해 처리한다.
- **TPM은 입력 토큰만 센다** (세션 2 실측). 출력·사고 토큰은 TPM을 쓰지 않는다.
- **모든 요청에 `thinking_config`를 명시한다.** 생략하면 Gemma 4는 사고가 켜진 채
  동작해 출력 한도를 사고로 다 쓴다 (세션 2 실측).
- secrets를 로그나 화면에 출력하지 않는다.

## 환경
- Windows 11, Python 3.12, 전역 pip (venv·uv 미사용 — 사용자 결정, 2026-08-13)
- 작업 폴더: C:\Users\user\Documents\claude_code\gchat
- 테스트: pytest / 린트: ruff / 타입: mypy

## v1 제외 (구현하지 말 것)
- 별도 설정 메뉴·패널. 컨트롤은 입력창 위 컨트롤 바에만 둔다 (2.6절)
- 최대 출력 토큰 UI. 모델별 고정 상수이며 TPM 계산용으로만 쓴다
- 대화 영구 저장 (DB, 파일 저장). 대화는 session_state에만 존재한다
- 파일 첨부 (2.9절 참고만)
- 웹 검색 그라운딩 (2.10절). 현재 키 등급에서 429로 막힌다. `tools` 파라미터를
  아예 쓰지 않는다
- Google OIDC 로그인
- 컨텍스트 요약 압축

## 금지
- `.streamlit/secrets.toml` 커밋
- temperature / top_p / top_k / candidate_count 전달 (어느 모델에도, 1.2절 결정)
- 요청 contents의 마지막 턴을 model 역할로 끝내기
- localStorage/sessionStorage 사용
- 서버 파일시스템에 대화 저장 (내보내기는 download_button만 사용)

## 진행 상황
- 세션 1 완료: 골격, `models.py`, `state.py`, `auth.py`, 최소 화면, 테스트
- 세션 2 완료: 실측(`scripts/probe_models.py` → `docs/api_findings.md`),
  `models.py` 실측 반영, 계획서 1.4·1.5·2.10절 갱신, `client.py`
- 다음: 세션 3 — `quota.py` + `context.py` (가짜 시계로 한도 시나리오 테스트)
- 남은 미해결: RPD 리셋 시각(부록 B-9), 가격 필드(수동 입력 필요)
