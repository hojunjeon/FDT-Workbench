# 구현 참고 문서

기존 엔진의 숫자 계약은 첨부된 FDT v0.1 코드·스키마·예제를 직접 사용했다. 웹 계층과 종료 수명 설계에는 다음 공식 문서를 참고했다. 확인일: 2026-09-07. 설치 버전은 문서 사이트의 최신 버전을 추종하지 않고 `requirements-web.lock`의 실제 테스트 버전으로 고정했다.

- FastAPI Request Files: https://fastapi.tiangolo.com/tutorial/request-files/
- FastAPI Static Files: https://fastapi.tiangolo.com/tutorial/static-files/
- Uvicorn Settings: https://uvicorn.dev/settings/
- Uvicorn Server Behavior: https://uvicorn.dev/server-behavior/
- Python multiprocessing: https://docs.python.org/3/library/multiprocessing.html
- Python signal: https://docs.python.org/3/library/signal.html

문서에 설명된 기능과 별개로 이 패키지의 통합 동작은 로컬 자동 테스트로 확인한다. Windows 실제 BAT 콘솔 테스트와 최초 네트워크 의존성 설치는 별도 미검증 항목이다.
