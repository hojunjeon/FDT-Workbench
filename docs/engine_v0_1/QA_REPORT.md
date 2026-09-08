# FDT Engine QA Report — v0.1

검증일: 2026-09-07. 대상: 루트 SPEC.md / PLAN.md를 기반으로 만든 독립 수치 엔진.
**구현 동작 검증과 금융 예측의 실세계 타당성 검증은 별개입니다.**

## 1. 실행 결과

|검사|실제 결과|근거|
|---|---|---|
|자동 단위·경계·계약·통합 테스트|**160 passed / 0 failed**|artifacts/pytest_output.txt, pytest.xml|
|엔진 statement coverage|**94.86%** (979/1032 statements)|artifacts/coverage.json|
|사용자 CSV 감사|4명·980건·각 32열·SEED, 원본 SHA-256 일치|data/manifest.json, artifacts/data_audit.json|
|소비와 7봉투 대사|4명 모두 차이 0원|docs/DATA_AUDIT.md|
|실제 첨부 데이터 4×5모드|**20/20 실행 및 result schema 검사 통과**|artifacts/demo/summary.json|
|CSV-only 절대 목표 판정|4명 모두 잔액을 발명하지 않고 insufficient_data|artifacts/demo/001~004/history_only_goal.json|
|독립 설치 스모크|**16/16 통과**, wheel을 빈 target에 설치·소스 밖 cwd에서 실행|artifacts/release_check.json|
|wheel 빌드·문법 검사|pip wheel / compileall 성공, 4개 schema 동봉|artifacts/wheel_build.txt|
|HTML/SVG 구조·안전성|자동 검사 4개 통과, 두 SVG의 래스터 변환·육안 확인|tests/test_renderer.py, artifacts/vector_visual_check.json|
|브라우저 전체 화면 QA|**미실행**: Playwright Chromium 실행 파일이 환경에 없음|artifacts/browser_check.json|

statement coverage는 분기 커버리지가 아닙니다. 설치 스모크의 별도 프로세스 실행은 위 coverage에
합산하지 않았습니다. `. __main__`의 pytest coverage가 0%여도 설치 스모크에서 `python -m fdt`를
실행한 사실과 모순되지 않습니다. 엔진 테스트와 별개인 검토용 renderer 전체의 커버리지를
측정했다는 뜻도 아닙니다.

## 2. 주요 테스트 내용

### 데이터와 모델

UTF-8 BOM/H:MM 형식, 부적합 금액·날짜·enum, 빈 자료/혼합 사용자/중복 충돌,
관측 cutoff 이후 행 배제, 0일 포함, 동일일 반복 지출 합산, 정기·비정기 소득 분리,
수동 일정의 추정 일정 대체, 미래 snapshot 거부, 미상 자산/부채와 순자산 null을 검사했습니다.
소비·정산 입금·저축/투자 이체·ATM·원리금·카드 대금은 구분합니다.

### 시뮬레이션과 5모드

같은 seed/입력의 재현성, 기준/분기의 common random numbers, 변경 없는 분기의 0차이,
여러 seed에서 변동소비 감축의 단조성, 금액/분위수/확률 범위, 월말 날짜,
신용 승인→미결제액→청구 출금, 승인/정산의 소비 중복 방지,
관리계좌 내부 이체 총액 보존, 이미 부족한 초기 상태,
총잔액은 양수지만 개별 결제계좌는 부족한 경우, 가용 현금에서 미결제액 차감,
부족 입력의 목표 보류, feasible/infeasible 탐색 및 불필요한 행동 0 선택을 검사했습니다.

### 이벤트·저장·입출력

동일 이벤트 no-op, ID 충돌, 사용자 불일치, 취소 원거래 확인, SEED 실행 경로 거부,
전체 batch 원자성/rollback, SQLite CAS, 갱신 뒤 snapshot dirty, 동일 날짜의 상태 경계,
snapshot 유효 여부를 포함한 input_digest, 실제 이벤트 fixture를 검사했습니다.
금융망을 호출한 테스트가 아니라 로컬 reducer/store 계약 검증입니다.

## 3. 첨부 데이터 20회 실행

모두 90일 × 400 paths × seed 42. 사용자별 초기 잔액/카드 정책은 **별도 입력한 데모 가정**입니다.
직접 측정한 한 번의 엔진 호출 시간 범위는 **0.063~0.539초**였습니다.
최적화 기본 후보는 3개 봉투 × 3개 감축률의 27가지 조합을 전수 평가합니다.
이 시간은 현재 Linux 환경에서의 단일 실행값이며 p95, 부하·동시성·SLA 검증이 아닙니다.

001은 예제 목표에 대해 추가 감축 없이도 제약을 충족해 0감축 후보를 선택했습니다.
002~004는 같은 예제 목표/후보/확률 제약 내에서 infeasible을 반환합니다.
이를 일반적인 개인 재무 평가로 읽으면 안 됩니다. 목표·잔액·카드 정책을 바꾸면 결과도 달라집니다.
`ok`와 `decision.feasibility=infeasible`은 동시에 가능합니다. 계산 자체는 정상 수행되었으나
지정한 후보 내 해가 없다는 뜻입니다.

## 4. 백테스트 — 제한적 모델 점검

각 사용자에 대해 2026-07-31 / 2026-08-15 시점까지만 학습하고 이후 14일을 비교했습니다.
4명 × 2 cutoff = **8개 구간**입니다. 기준선은 직전 28개 달력일의 일평균 × 14일입니다.

|지표|FDT 모델|단순 직전 28일 기준선|
|---|---:|---:|
|14일 소비 합계 MAE|458,136원|616,426원|
|14일 자금 여력 변화 MAE|393,642원|1,355,727원|

모델 P10~P90 구간에 실제 합계가 들어온 비율: 소비 **6/8=75%**, 자금 여력 **6/8=75%**.
명목 구간 질량은 80%입니다. **75%를 예측 정확도라고 표기하면 안 됩니다.**
원시 window별 예측/관측/기준선 값은 `artifacts/backtest.json`에 있습니다.

8개의 합성 구간, 동일 사용자 반복 평가, 짧은 이력으로 실세계 확률 보정을 주장할 수 없습니다.
모델 설계 시 열람한 합성 자료이므로 완전히 독립적인 외부 검증 코호트도 아닙니다.
실제 잔액/미납 여부가 없어 잔액 예측 오차나 실제 연체 확률은 검증하지 않았습니다.
`model.calibrated=false`를 모든 output에서 유지합니다.

## 5. 시각화 검토

수치의 진실은 JSON이며 ChartSpec은 어느 dataset/field를 어떻게 표시할지 지정합니다.
HTML 어댑터는 escaping/CSP를 적용하고 외부 JS/CDN을 쓰지 않습니다.
두 SVG를 CairoSVG로 래스터화하여 선·band·확률축·후보 표시를 확인했습니다.
첫 래스터에서 마지막 날짜 라벨이 잘리는 것을 발견해 우측 여백을 늘리고 재확인했습니다.
`artifacts/forecast_chart_qa.png`, `optimization_chart_qa.png`는 **추출 SVG** 검토본이며
브라우저 스크린샷이 아닙니다. HTML 전체 레이아웃/모바일/접근성 브라우저 검토는 남아 있습니다.
CairoSVG/Playwright는 개발 환경의 점검 도구였으며 엔진 의존성에 추가하지 않았습니다.

## 6. 설치 검증 환경

- Python 3.13.5; Linux-6.18.35-x86_64-with-glibc2.41.
- NumPy 2.3.5, jsonschema 4.26.0, pytest 9.0.2, pytest-cov 7.0.0.
- wheel: `keyfin_fdt_engine-0.1.0-py3-none-any.whl`.
- wheel SHA-256: `2ed7769857f1f47881fd8279217024cc58237acda7579dc7942536a2ff5e50c1`.

`--no-index --no-deps --target <빈 디렉터리>`로 wheel을 설치하고 원본 소스 밖에서
CLI 생성/조회/5모드/CSV-only/이벤트/중복 재수신/CAS 오류/이후 예측을 검증했습니다.
단, NumPy 등 런타임 의존성은 기존 인터프리터에서 가져왔습니다. 완전히 빈 가상환경에서
인터넷 의존성 설치를 검증하거나 Windows/macOS를 실기기 검증한 것은 아닙니다.

## 7. 현재 경계와 배포 전 추가 검토

이 릴리스는 **오프라인 개인 현금흐름 FDT v0.1**입니다. 투자·부채 잔액 수신과 투자 평가액
정적 shock은 있지만 투자수익률/공분산, 대출 원리금 상환표, 보험 보장 분석, 인과적 행동
반응 모델은 구현하지 않았습니다. 일 단위이므로 당일 거래시각 순서/휴일 규칙도 별도입니다.
최적성은 유한 후보 그리드 내에서만 성립하며 실제 행동/이체/상품 거래는 하지 않습니다.

SQLite/CSV는 평문 개발 저장물입니다. 인증·접근 제어·키관리·암호화·동의·관측 진위·
운영 감사/보존·장애복구는 배포 호스트의 추가 설계가 필요합니다.
실제 금융기관 연동/대규모 동시성/독립 보안감사/실사용자 calibration은 수행하지 않았습니다.

## 8. ZIP 검증

ZIP 루트의 SPEC.md/PLAN.md, 소스, 네 입력, 예제, 결과, wheel의 포함 여부와 CRC를 검사합니다.
별도 디렉터리에 압축을 풀어 소스 테스트/임포트와 CLI 실행을 확인한 결과는
`artifacts/package_check.json`, `artifacts/unpacked_pytest_output.txt`를 최종 기준으로 합니다.
`FILE_MANIFEST.json`은 패키지 파일의 SHA-256 목록이며 자기 자신은 제외합니다.
