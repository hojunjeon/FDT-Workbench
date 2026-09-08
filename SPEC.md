# SPEC: KeyFin FDT Workbench v0.2

## 목적과 경계

사용자가 제공한 FDT 엔진 v0.1 ZIP을 그대로 기반으로 CSV 생성 페이지 1개, 수동 모드 실행 페이지 5개, 결과 JSON 표시, Windows 통합 BAT 실행·Ctrl+C 종료를 추가한다. 금융 계산 규칙, 데이터 매핑, RandomBundle, Engine.run, 스냅샷의 의미는 수정하지 않는다. 원 엔진 SPEC은 `docs/engine_v0_1/SPEC.md`에 보존한다.

자연어 라우터·LLM·실제 이체·은행 연결·운영 배포·로그인·투자수익률/보험 모델 확장은 범위 밖이다.

## 화면 계약

| ID | 계약 |
|---|---|
| WEB-01 | `#/build`: UTF-8 CSV 1~4개, 합계 8 MB, 사용자 1명, 거래 10,000건. 서버 검증 후 사용자/기간/계좌/카드/중복 수를 미리보기한다. |
| WEB-02 | 이력 전용, 간편 스냅샷 폼, 원문 스냅샷 JSON의 세 입력 방식. 간편 입력은 USER_ASSUMPTION이며 알 수 없는 잔액을 0으로 생성하지 않는다. |
| WEB-03 | 데모 001~004와 스냅샷을 불러오는 버튼. 데모 가정 여부를 표시한다. |
| WEB-04 | `#/forecast`, `#/what_if`, `#/goal`, `#/risk`, `#/optimize`: 각 모드 전용 폼과 전체 JSON 편집. 기존 `fdt/schemas/request.json`을 서버에서 검증한다. |
| WEB-05 | 공통 기간/경로/시드, What-if 감축률·소득·물가·충격·현금 이벤트·일정 취소, 목표 조건, 위험 시나리오, 최적화 후보 및 제약 입력. 간편 폼의 비율은 %이며 JSON은 소수 비율이다. |
| WEB-06 | 입력 JSON 파일 불러오기/저장, 기본값 복구, 폼/JSON 전환. JSON→폼은 먼저 스키마 검증한다. mode는 페이지와 일치해야 한다. |
| WEB-07 | 결과 JSON 원문, 핵심 수치, 시각화, 가정/경고, 실제 실행 요청의 5개 결과 탭. 복사 및 JSON 다운로드. |
| WEB-08 | 결과는 입력·Twin·기준일·경과 시간을 표시한다. 입력 변경 시 이전 결과임을 알린다. partial/insufficient_data/infeasible을 임의 성공 판정으로 바꾸지 않는다. |
| WEB-09 | 실제 실행 시간 표시, 작업 취소, 서버 작업 재발견을 통한 새로고침 복구. 임의의 완료 비율을 만들지 않는다. |
| WEB-10 | 저장된 Twin 목록·선택·상태 JSON 저장·확인 후 삭제. 활성 Twin의 UI 식별자는 UUID이며 엔진 twin_id를 바꾸지 않는다. |
| WEB-11 | 데스크톱 2열 및 모바일 1열 배치. 긴 JSON은 해당 패널 내부에서 스크롤한다. |

## 요청과 숫자 정합성

실행 경로는 Browser → FastAPI → spawned numeric worker → Engine.run(request) → result.json → Browser이다. 결과 원문은 Engine 결과 객체 그대로이며 API는 수치 계산을 다시 수행하거나 LLM을 호출하지 않는다.

필수 엔진 계약:
- 금융 입력 금액은 원 단위 정수이다. 경로/기간/시드는 엔진 스키마 한도를 따른다.
- 기준일은 마지막 관측일이 기본이며 스냅샷 기준일 및 미래 거래 처리는 기존 엔진을 따른다.
- unknown/null은 0과 다르다. 시각화에서도 gap 또는 추가 입력 안내로 남긴다.
- 같은 사용자 CSV를 합쳐도 거래 ID의 중복 충돌 규칙을 지킨다.
- 그래프는 result.visualizations가 가리키는 result.datasets의 필드만 사용한다.
- 조건부 모델 확률, 가정 잔액, 미보정 상태 `calibrated=false`를 유지한다.
- 최적화는 최대 128 유한 후보와 원 엔진 연산량 제한을 유지한다.

## API 계약

| HTTP | 경로 | 기능 |
|---|---|---|
| GET | `/api/health` | 실행 상태, 버전, 서버 PID |
| GET | `/api/config` | 모드 템플릿, JSON 스키마, 카테고리, 업로드 한도, 세션 쓰기 토큰 |
| GET | `/api/demo/{001..004}/{csv\|snapshot}` | 로컬 데모 파일 |
| POST | `/api/uploads` | multipart `files` → upload_id와 CSV profile |
| DELETE | `/api/uploads` | 계산 중이 아닐 때 임시 업로드 정리 |
| GET/POST | `/api/twins` | 목록 / 생성 job 제출 |
| GET/DELETE | `/api/twins/{id}` | inspect / 확인된 요청에 따른 로컬 삭제 |
| POST | `/api/validate/request` | 엔진 Request 스키마 검증 |
| POST | `/api/twins/{id}/runs` | 엔진 Request 직접 전달, 비동기 job 반환 |
| GET | `/api/activity` | 현재 실행 중인 작업과 복구용 입력 |
| GET | `/api/jobs/{id}` | 작업 상태 및 elapsed_seconds |
| POST | `/api/jobs/{id}/cancel` | 해당 작업의 프로세스만 종료 |
| GET | `/api/jobs/{id}/result` | 성공적으로 완료된 엔진 결과 원문 JSON |

작업 상태: `running → succeeded / failed / cancelled`. 완료 결과의 `status`(ok/partial/insufficient_data)와 작업 상태(succeeded)는 별개다. 계산이 정상 종료되어도 금융 판정에 필요한 자료가 부족할 수 있다.

입력 오류는 `{"status":"error","error":{"code":...,"message":...,"details":...}}` 형식이다. HTTP는 형식·스키마 422, 미존재 404, 실행 충돌 409, 호스트·Origin·토큰 거부 403, 전체 본문 초과 413을 사용한다. CSV 내부 합계 8 MB 초과는 엔진형 입력 오류 422일 수 있다.

## 실행기와 프로세스 수명

- START.bat는 자기 위치로 이동하고 .venv를 만들며 그 가상환경 Python으로 launcher.py를 **전경 실행**한다. PowerShell 활성화나 실행 정책 변경이 필요 없다.
- launcher.py는 테스트한 의존성 버전이 없을 때만 해당 가상환경에 설치한다. 첫 설치는 네트워크가 필요하며 시스템 Python에 자동 설치하지 않는다.
- 프론트와 API는 동일 Uvicorn 프로세스로 제공한다. 계산은 `multiprocessing`의 `spawn` 자식으로 분리한다. Node 개발 서버와 리로더는 사용하지 않는다.
- 동시 작업은 1개. 계산 제한 시간은 300초. 이후 해당 자식만 종료한다.
- 첫 Ctrl+C는 서버 종료 절차를 시작한다. lifespan 정리에서 진행 중인 작업을 terminate→join하며 필요한 경우 kill→join한다. 소켓과 임시 파일을 정리하고 저장된 Twin은 유지한다.
- 수치 자식은 콘솔 그룹의 SIGINT/SIGBREAK를 무시하고 부모가 수명을 관리한다. Windows와 POSIX에서 같은 spawn 경로를 사용한다.
- 같은 데이터 디렉터리의 중복 실행은 OS 파일 잠금으로 거절한다.
- 기본 8765가 점유되면 8784까지 탐색한다. 명시 포트 점유는 오류이며 다른 프로그램을 강제 종료하지 않는다.

## 저장·안전성

Twin 생성 작업은 작업 폴더에 임시 SQLite를 작성한다. 성공한 작업만 부모가 metadata와 함께 twins 디렉터리로 이동하여 커밋한다. 취소·실패 생성은 영구 Twin 목록에 등록하지 않는다.

세션 작업 결과는 최근 최대 30건으로 제한하고 정상 종료/다음 시작에 정리한다. CSV staging은 수동 정리/24시간 만료/종료/시작 시 정리한다. Twin DB는 명시적 삭제 전까지 유지한다.

호스트는 loopback만 허용하며 hostile Host/Origin과 세션 쓰기 토큰을 검사한다. 사용자 파일명은 실제 저장 경로로 사용하지 않는다. 응답은 no-store, nosniff, CSP를 설정하고 외부 스크립트·CDN·폰트를 사용하지 않는다. UI는 사용자 문자열을 HTML escape한다.

이는 로컬 단일 사용자 도구다. 암호화 저장, 인증·운영권한·TLS는 미구현이며 운영 서비스에 그대로 노출하지 않는다.

## 인수 기준

1. 첨부 데이터 4종으로 웹 경로에서 Twin 생성 및 5모드 실행.
2. 같은 저장 Twin과 Request로 웹 결과와 직접 Engine.run 결과의 객체 완전 일치.
3. 스냅샷 없는 절대 잔액 null 및 목표/최적화 insufficient_data 보존.
4. 잘못된 CSV/JSON/사용자 혼합/스키마 범위 위반은 정형 오류로 반환.
5. 작업 취소·실패 시 영구 저장소 오염 없음, 서버는 후속 요청 수신.
6. Ctrl+C 후 자식 프로세스가 남지 않고 같은 포트로 재실행 가능.
7. 저장된 Twin은 종료·재시작 후 유지, 임시 데이터는 정리.
8. 모든 페이지에서 입력 수정과 JSON 표시, 시각화, 좁은 화면의 가로 넘침 점검.
9. Windows 실제 PC 검증과 이 환경의 Linux/브라우저 하네스 검증을 혼동하지 않고 QA 보고서에 구분.
