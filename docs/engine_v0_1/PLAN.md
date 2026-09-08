# FDT Engine PLAN — SPEC v0.1 기반

## 1. 진행 원칙

SPEC를 먼저 확정하고 구현한다. 수정이 필요하면 SPEC/코드/테스트를 같이 바꾼다.
첨부 원문은 보존하며 신규 모델 판단을 원문 요구사항으로 둔갑시키지 않는다.
금융 실행 없는 수치 엔진에 집중한다. LLM/서버/UI 제품 구현을 끼워 넣지 않는다.

## 2. 구현 순서와 검토 gate

|단계|구현|검토|완료 증거|
|---|---|---|---|
|P0 입력 감사|CSV 4개 구조/기간/ID/금액/분류 분석, 원본 해시|잔액 부재, SEED, 32열, 이중계상 위험|data/manifest.json, docs/DATA_AUDIT.md|
|P1 계약|SPEC, request/snapshot/result schemas|unknown, 금액 단위, enum, 필수값|SPEC.md, schemas contract tests|
|P2 ingestion|거래 정규화/봉투 매핑/중복검사|중복 ID, 인출/저축/원리금/정산 구분|tests/test_ingest.py|
|P3 twin/model|관계 graph, snapshot gates, 반복 일정, 행동 벡터|기간 중간 자료, 0일, 미래 누수, 중복 반복|tests/test_model.py|
|P4 simulation|블록 샘플, 반복/수동 일정, 카드/계좌 전이|명시적 정책, 출금 vs 소비, CRN, int KRW|tests/test_simulation.py|
|P5 modes|5모드, 구조화 metrics/datasets/visualizations|목표 정의, risk unknown, 유한 grid 최적성|tests/test_modes.py|
|P6 lifecycle|SQLite create/load/update, 이벤트 batch|idempotency, rollback, optimistic revision|tests/test_store.py|
|P7 adapter|CLI, 요청 예제, 오프라인 보고서|엔진 외부 계산 없음, 안전한 HTML escaping|tests/test_cli.py|
|P8 QA|unit/integration/contract, 4명×5모드, holdout backtest|실패를 숨기지 않음, 확률 보정 미보장|QA_REPORT.md, artifacts/*.json|
|P9 패키지|README, 예제, ZIP 설치/스모크|새 디렉터리 실행, 비밀키/임시 DB 미포함|artifacts/release_check.json|

## 3. 구현 구조

- `fdt/ingest.py`: CSV 및 공통 거래 adapter, raw provenance, normalization.
- `fdt/model.py`: Twin create, snapshot validate, recurring/behavior model.
- `fdt/simulation.py`: 난수 bundle/branch 변환/계좌·카드 상태 전이.
- `fdt/engine.py`: 5모드 수치 interface, 결과 계약 및 시각화 binding.
- `fdt/store.py`: SQLite persist, batch event ingest, CAS.
- `fdt/cli.py`: build/inspect/run/update/list-modes.
- `fdt/schemas/*.json`: 검증 가능한 입력/출력 계약.
- `scripts/run_demo.py`, `scripts/backtest.py`, `scripts/render_report.py`.

## 4. 구체적 테스트 계획

### 4.1 단위/경계

CSV: UTF-8 BOM, H:MM, 음수/소수/NaN, 잘못된 enum/날짜, 빈 입력, 동일/충돌 ID,
혼합 사용자, 알려지지 않은 분류 fallback, PENDING, SEED/LIVE.
정합성: spend의 7봉투 합계 일치, internal savings/ATM 제외, 카드 settlement 제외,
원리금 미분리를 unknown으로 유지.
모델: landlord 2행/동일일 합산, family support 월별 추정, 28일 진료 간격,
관측되지 않은 현재 잔액 금지, 사용자 날짜 기준, 손실 없는 정규화.
시뮬레이터: 0변경 동일, 변동지출 감축 단조성, 금액 정수, 분위수 순서,
card purchase → payable → bill 정산, known bills, 총액 충분/개별계좌 부족,
월말 일자 clamp, 소득 없는 경우, reserve, 초기 잔액 부족.

### 4.2 계약/통합

모든 5모드 output을 JSON schema로 검증하고 JSON 직렬화 allow_nan=False 검사.
ChartSpec dataset/field와 실제 자료를 대조한다. 확률은 [0,1], 필드 unit 확인.
각 CSV에 history-only 실행 + 명시적 demo snapshot 5모드 실행.
목표가 불가능한 케이스, 최적화 feasible/infeasible, 고정비 비삭감 케이스 포함.

### 4.3 영속화/보안

run 읽기 전용, duplicate event no-op, conflict rollback, batch 원자성,
잘못된 user_id, 늦은 stale writer(CAS) 거부. SQL은 parameter binding.
입력 경로/JSON을 eval/pickle/shell로 실행하지 않는다. 시각화 HTML은 escape.
API 자격증명, 네트워크 의존, 개인 비밀 데이터 없음 확인.

### 4.4 모델 QA

각 사용자 2026-07-31/2026-08-15 cutoff, 이후 14일 holdout: 소비 합계와
resource change의 MAE/분위수 포함 여부를 기록한다. 학습에 cutoff 이후 행 사용 금지.
결과 좋음을 합격 조건으로 꾸미지 않는다. 8개 합성 window로 실제 확률 보정 판단 불가.
운영 도입 전 실제 데이터와 더 긴 rolling 검증/단순 baseline 비교/모델 선택 필요.

### 4.5 성능/패키지

대표 4×5 실행 시간을 perf_counter로 기록한다. elapsed는 숫자 결과 digest에 넣지 않는다.
최대 job 크기/후보수 거부 테스트로 자원 폭주를 방지한다.
실행한 Python/NumPy/jsonschema/pytest 버전을 기록하고 재현용 lock을 제공한다.
ZIP을 새 디렉터리에 풀어 wheel/build 또는 offline --no-deps 설치 후 CLI smoke를 실행한다.
Linux 환경 검증이며 Windows/macOS 실기기 검증은 했다고 쓰지 않는다.

## 5. 완료/미완료 표기 규칙

체크리스트는 최종 `QA_REPORT.md`의 실제 실행 결과와 연결한다.
'계획됨'을 '실행됨'으로 쓰지 않는다. 통과 수, 실패 수, 커버리지(측정한 경우만),
backtest 원시 결과, known limitations를 공개한다.

## 6. 실행 완료 상태 (2026-09-07)

|단계|상태|확인|
|---|---|---|
|P0~P1 입력/계약|완료|980행 감사, 원본 해시, SPEC/JSON schemas|
|P2~P4 ingestion/model/simulation|완료|정합성/일정/카드/재표본추출 테스트|
|P5~P7 모드/lifecycle/adapter|완료|5모드, SQLite/CAS, CLI, JSON+ChartSpec, 오프라인 HTML 어댑터|
|P8 QA|완료(범위 내)|160 자동 테스트, 20 모드 실행, 8개 제한적 backtest|
|P9 패키지|완료(검사 기록 기준)|wheel 독립 설치 16검사, ZIP 압축해제 검증 기록|
|전체 브라우저 UI QA|미실행|Chromium 바이너리 부재; SVG 추출 래스터만 검토|
|실사용자 확률 검증/기관 연동/운영 보안감사|미실행·후속 단계|QA_REPORT의 한계와 추가 gate 참조|

구체적인 실행 수치와 실패/미실행 사항은 `QA_REPORT.md` 및 `artifacts/`의 실제 기록이 우선한다.
