# FDT Engine SPEC — v0.1

작성일: 2026-09-07 · 범위: 독립 Financial Digital Twin 수치 엔진

## 1. 요청과 경계

사용자 요청을 다음 계약으로 고정한다.

`소비 CSV → 사용자별 Twin 생성 → 수동 mode 선택 → 정형 request → 수치 result + visualization spec`

외부 AI 에이전트는 향후 `run(request)`만 호출한다. 자연어 라우팅, LLM,
코칭 문장, 앱·인증·푸시·게이미피케이션·금융망 쓰기 API는 구현하지 않는다.
실제 이체, 결제, 상품 추천도 하지 않는다. 엔진에 실행 권한이나 API 키는 없다.

5개 모드는 `forecast`, `what_if`, `goal`, `risk`, `optimize`이다.
생성·조회·이벤트 갱신·저장은 lifecycle이지 별도의 분석 모드가 아니다.

## 2. 출처와 설계 판단 분리

### 2.1 첨부 자료에서 채택한 제약

- `source_materials/01_KeyFin_기획의도(2).md`: 소비 가이드, 정기 지출 준비,
  금액과 판정은 엔진이 산출하는 경계.
- `source_materials/02_KeyFin_요구사항명세(2).md`: FR-TXN-02·10(세분류/봉투),
  FR-BGT-03(승인과 청구의 중복 방지), FR-PAY-01·02(결제 일정/필요액),
  NFR-BGT-01(정합성), NFR-TXN-02(SEED/LIVE 분리), FR-AI-02(숫자는 엔진).
- 첨부 `금융_api.zip`의 `api-credit-card.md` §2.8.12: 주간 승인분은 차주 월요일
  07:30에 청구서 발행. **발행일과 실제 출금일은 다르다.** 이 엔진은 일 단위이고,
  데모 신용카드 정책의 `payment_delay_days`는 발행일 이후 출금까지의 명시적 가정이다.
- 같은 ZIP의 `api-demand-deposit.md` §2.4.7은 `accountBalance`를 제공하지만
  소비 CSV에는 이 값이 없다. 대출 API의 `loanBalance`를 잔여 원금으로 임의 해석하지 않는다.
- 페르소나 TXT는 맥락 참고만 한다. 이름·나이·직업으로 금액이나 성향을 생성하지 않는다.

### 2.2 실제 입력 확인

|파일|CSV의 실제 user_id|건수|관측 기간|
|---|---|---:|---|
|소비001|USR-DEMO-002|337|2026-06-06 ~ 2026-09-03|
|소비002|USR-DEMO-003|193|2026-06-08 ~ 2026-09-03|
|소비003|USR-DEMO-004|262|2026-06-06 ~ 2026-09-03|
|소비004|USR-DEMO-005|188|2026-06-06 ~ 2026-09-02|

합계 980건, 32열, 모두 SEED. 파일 번호와 실제 persona/user 번호는 다르다.
원본 이름/해시/행 수는 `data/manifest.json`이 기준이다.
CSV의 분류는 기획서의 7봉투/22세분류와 동일하지 않으므로 원본을 보존하고
버전이 있는 명시적 매핑을 적용한다. 매핑은 **본 구현의 설계 판단**이지 원문 규칙이 아니다.
특히 주거·보험은 기타로 매핑하되 fixed 플래그를 별도로 유지한다.

### 2.3 신규 설계 선택

Python 3.11+, NumPy 수치 계산, JSON Schema 검증, SQLite 로컬 영속화.
외부 네트워크 없이 작동한다. 반복 거래 추정 + 요일 정렬 7일 블록 bootstrap을 사용한다.
90일 정도의 합성 데이터는 확률 보정을 증명하지 못한다. 모든 확률/구간은
**관측 이력이 반복된다는 가정 아래의 모델 조건부 시뮬레이션 값**이다.

## 3. Twin 정의와 데이터 계약

`Twin = (Observation ledger, Snapshot, Relationships, Behavior, Transition, Uncertainty, Version)`

- Observation ledger: 정규화 거래 + 원문 분류 + 파일/행 근거. SEED는 행동 추정에만 사용.
- Snapshot: 기준일 마감 계좌 잔액, 카드 종류/연결계좌/미결제액, 자산·부채, 선택적 일정/예산.
- Relationships: user→account/card, card→settlement_account, transaction→merchant/envelope.
- Behavior: 요일별 잔여 소비 벡터, 반복 수입·지출, CSV의 IMPULSE 라벨 통계.
- Transition: 하루별 수입, 계좌 지출, 카드 승인/청구, 저축·인출의 상태 전이.
- Uncertainty: 과거 블록 재표본추출과 반복 금액 재표본추출. 같은 난수로 분기 비교.
- Version: canonical content SHA-256(거래·snapshot·snapshot_dirty 포함) + model_version + mapping_version + 이벤트 revision.

### 3.1 현재값을 모르는 것은 null이다

CSV 합계에서 현재 잔액을 역산하지 않는다. 거래가 없는 날은 관측 기간 내 무거래일로
명시적으로 채우지만, 이는 수집의 완전성이 보증되었다는 뜻이 아니다.
잔액이 없으면 `cash_balance_*`, `p_cash_shortfall`, 목표 확률은 null/insufficient_data.
대신 구매시점 기준 `resource_change_*`와 소비 분포, 반복 일정은 계산한다.

`resource_change = income + reimbursement − consumption − debt_service − savings_out − cash_withdrawal`

이 값은 **관리 계좌의 지출 준비 여력 변화 프록시**이며 현금 잔액·순자산이 아니다.
대출 원리금 분해, 미관측 현금 지출, 투자 수익을 포함한 총재산 변화로 해석하면 안 된다.

절대 현금 경로를 계산하려면 모든 관측 결제 채널의 잔액/정산 정책이 필요하다.
스냅샷 기준일은 Twin 기준일과 정확히 같아야 한다. 오래된 스냅샷을 자동 이월하지 않는다.
스냅샷 `source=USER_ASSUMPTION`은 결과에 그대로 노출한다.

### 3.2 거래 정규화

금액은 0 이상 정수 KRW, 방향이 부호를 결정한다. 관측/예측 누적 금액은
9×10^15 안전 한도를 검사하여 int64 overflow 및 JSON 정수 정밀도 손실을 거부한다. 잘못된 날짜·금액·enum·혼합 사용자는 거부.
동일 ID/동일 내용은 중복 제거; 동일 ID/다른 내용은 충돌 오류. 파일 순서와 거래 순서에 무관.

|입력 의미|내부 kind|소비 반영|현금/여력 의미|
|---|---|---|---|
|일반 구매|expense|7봉투 중 정확히 하나|계좌/체크는 즉시, 신용은 정산 시 출금|
|수입|income|아니오|입금|
|모임 정산 입금|reimbursement|아니오|수입과 구분한 유입; 원거래 연결 없으면 봉투 상계 안 함|
|적금/증권 이체|savings_out|아니오|관리 계좌 밖으로 이동; 자산 소실 아님|
|ATM 인출|cash_withdrawal|아니오|관리 은행계좌 감소; 실제 현금 소비는 미관측|
|원리금 납부|debt_service|아니오|계좌 출금, 이자/원금 미분리|
|카드 대금 납부|card_settlement|아니오|역사 소비에 재합산하지 않음|
|명시적 본인계좌 이체|internal_transfer|아니오|양 계좌 확인 시 총현금 보존|
|CANCELED|inactive|아니오|학습에서 제외|

`INTERNAL_TRANSFER`는 원장 `SELF_TRANSFER`와 별도 보존/정규화한다.
DUTCH·EMERGENCY·CARRYOVER 태그는 소비 현금흐름 자체를 삭제하지 않는다.
봉투 제외 태그는 `budget_amount_krw=0`으로 별도 처리한다. 정산 입금에 원거래 ID가 없는
첨부 CSV를 보고 임의로 환급 매칭하지 않는다. PENDING은 잠정 분류라는 경고와 함께 포함.
부분 취소·환불 자동 매칭은 v0.1에서 미지원; 명시적 전체 취소 이벤트만 처리한다.

### 3.3 선택 스냅샷

`fdt/schemas/snapshot.json`이 기계 계약이다. 계좌, 카드, known_bills,
수동 schedules, assets, liabilities, reserve_krw, budgets를 입력한다.
모든 신용카드는 opening_payable_krw와 동일 총액의 known_bills가 필요하다.
카드 출금계좌는 알려진 관리계좌여야 한다. 현금 모델에 누락 채널이 있으면 부분 결과로 전환.
순자산 현재값은 자산/부채 전체 보고 플래그가 모두 참이고 모든 금액이 알려질 때만 계산한다.
보험료는 지출 일정으로 다루며 보장 적정성은 판정하지 않는다.
투자·부채 잔액은 snapshot/외부 shock의 대상이다. CSV만으로 투자 수익률,
대출 금리, 부채 잔여 원금, 보험 보장을 복원하거나 예측하지 않는다.

## 4. 모델 구축

### 4.1 기간과 누수 방지

기준일 기본값은 최신 거래일의 마감이다(시스템 오늘 아님).
`as_of`를 주면 이후 거래는 학습에서 제외하고 제외 건수를 기록한다.
행동 통계 분모는 최초 관측일~기준일의 전체 달력일이다. 월 중간 자료를 완전한 한 달로 취급하지 않는다.
상한은 10,000 거래, 1,096일 이력, 365일 예측, 2,000 paths이다.
메모리 보호를 위해 paths × horizon ≤ 400,000, optimize 후보 ≤ 128을 강제한다.

### 4.2 반복 일정

가맹점·kind·세분류·결제 채널로 그룹화한다. 같은 날짜 복수 결제는 합산한다.
동일 가맹점의 비정기 프로젝트 수입과 정기 유지보수 수입은 recurring 힌트로 분리한다.
2회 이상 + recurring 힌트, 또는 3회 이상 반복 증거가 있어야 반복 추정 후보가 된다.
정확한 7/14/28일 간격은 interval, 서로 다른 달의 유사한 날짜는 monthly로 추정한다.
월말은 해당 달의 마지막 날로 clamp한다. 공휴일/은행 영업일 달력은 미지원이며 경고한다.
추정 일정은 confidence와 evidence 거래 ID를 가진다. 계약 확정 일정이 아니다.
수동 schedules에 `replaces_rule_id`가 있으면 추정 일정을 교체하여 이중 계상하지 않는다.
반복 일정으로 분리된 거래는 stochastic pool에서 제거한다.

### 4.3 확률·행동 모델

잔여 일별 벡터는 소비 봉투×결제 채널, 수입, 상환, 저축, 인출을 함께 보유한다.
미래 7일 블록마다 같은 시작 요일의 과거 연속 7일 벡터를 샘플링한다.
동일 블록의 채널을 함께 추출하므로 블록 내 시간·수입/지출 상관은 보존되지만,
블록 사이·장기·계절·구조 변화의 상관은 보존하지 못한다.
짧은 이력은 요일별 일 표본으로 fallback하고 데이터 품질 경고를 준다.
반복 금액은 관측 금액 표본에서 재추출; 날짜는 추정 일정에 따른다.
행동 라벨 통계는 CSV의 `spend_pattern`에서 나온 설명 지표이지 인과 모델이 아니다.

### 4.4 일별 상태 전이

계좌 k: `C[k,t+1] = C[k,t] + incoming − direct_outgoing − card_bill + internal_net`.
신용카드 j: `P[j,t+1] = P[j,t] + purchases − billed_payment`.
소비 봉투는 승인일에만 증가하고 카드 청구 출금에는 증가하지 않는다.
일간 현금이 음수가 되어도 0으로 clip하지 않는다. 이것은 **미충족 자금 수요**이며
실제 은행이 마이너스 잔액 출금을 허용한다는 뜻이 아니다.
하루 안의 시간순 잔액 부족은 모델링하지 않고, 기준일 잔액도 위험 검사에 포함한다.
`unencumbered_liquid = total_managed_cash − total_card_payable`.
목표는 이 값에서 reserve를 제외하고 평가한다. 총합 충분/개별계좌 부족은 별도 위험이다.

## 5. 5개 모드

공통 request: `mode, horizon_days, paths, seed`, 선택 `scenario`.
모드는 enum으로 직접 선택하며 자연어 파싱을 하지 않는다.

### M1 forecast

기준 시나리오의 일별 P10/P50/P90 소비, resource_change, 현금 잔액, 카드 미결제액,
청구/고정 일정, 봉투별 소비 분포를 반환한다. 청구일이 horizon 밖이어도 이미 모의 승인된
청구는 calendar에 표시하되 within_forecast_horizon=false로 구분한다. 경계 주의 금액에는
horizon 이후 신규 승인이 포함되지 않으므로 완전한 차주 청구액이 아니다. 경로 percentile은 모델 예측 분위수이지
모수 신뢰구간이 아니다. 금액/날짜/근거를 함께 제공한다.

### M2 what_if

baseline과 branch에 **같은 난수 경로**를 적용한다. 지원 intervention:
- 봉투별 변동 소비 축소율(0~1). fixed/recurring에는 미적용.
- income_multiplier, expense_multiplier(사용자가 입력한 외부 상황 가정).
- 알려진 반복 일정 중 cancel_rule_ids.
- 일회성 입출금(날짜, 계좌, amount_krw, direction).
- asset_shock_fraction(명시적으로 제공한 투자 평가액의 시점 충격; 현금과 구분).

월세 지연·대출 갈아타기·투자상품 선택을 자동 창작하지 않는다.
비교는 경로별 차이의 분포를 산출한다(분위수의 단순 차이와 구별).
원 Twin과 기준 시나리오는 변경하지 않는다.

### M3 goal

`goal.target_krw`, `goal.reserve_krw`, `goal.success_probability`와 horizon으로
만기일 목표를 정의한다. 투자/부동산 처분은 하지 않는 liquid goal이다.
`P(unencumbered_liquid_end − reserve ≥ target)`와 기간 중 부족이 없는 공동확률,
부족액 P50/P90, 일정 간격 추가 외부 수입이 있을 때 필요한 금액을 계산한다.
추가 외부 수입 등가액은 새로운 돈을 넣는 조건이며 '저축만 더 하면 생기는 돈'이 아니다.
현재 잔액 미상 시 확률은 null, `insufficient_data` 및 필요한 입력 목록을 반환한다.

### M4 risk

총/계좌별 현금 음수 확률, 최대 부족액 분포, 첫 부족일 분포, reserve 미달,
현재월 잔여기간의 봉투 예산 초과 확률(예산 입력 시), 수입 변동성·지원 의존 비중,
사용자 입력 stress 시나리오 민감도와 선택 투자 평가액 shock을 반환한다.
통계적 위험 지표이지 연체 확정/신용점수/투자 손실 보증이 아니다.

### M5 optimize

사용자가 지정한 변동 소비 봉투와 감축 grid를 유한 열거한다. 기본 후보는
외식·취미·여가·쇼핑 각각 0/10/20% 감축(27개).
의료·건강 등 보호 봉투는 기본적으로 탐색하지 않는다. 고정비 자동 삭감 없음.
제약: 목표 공동성공확률 하한, 현금 부족확률 상한, reserve, 봉투별 최소 잔여 소비.
가능 후보 중 기대 절감액이 가장 적은 후보를 선택(최소 행동 변경 대용 목적함수).
목표가 없으면 부족 제약을 만족시키는 최소 변경; 이미 만족 시 무변경이 정답이다.
충족 후보가 없으면 `infeasible`을 반환하고 권고를 발명하지 않는다.
최적성은 **명시된 유한 grid/동일 표본/목적함수 내에서만** 성립한다. 전체 금융행동의 전역 최적 아님.

## 6. Output / 시각화 계약

공통 envelope: schema_version, mode, status, twin_id, revision, as_of, horizon_days,
model, input_digest, assumptions, warnings, limitations, metrics, datasets, visualizations.
수치/자료에는 KRW, probability, date, count 단위를 붙인다. unknown은 null.
결과에 raw PII/메모를 자동 노출하지 않는다. identity는 opaque user_id로 한정한다.
요청 및 결과는 strict JSON(NaN/Infinity 불가). 알 수 없는 요청 필드는 오류.

|모드|시각화|dataset과 축|
|---|---|---|
|forecast|불확실성 band + 중앙선|date × cash/resource P10/P50/P90|
|what_if|기준/분기 선, 차이 표|date × baseline/branch; paired delta|
|goal|목표선+분포, 부족액 표|terminal free funds와 target; probability [0,1]|
|risk|부족확률 선, 계좌별 막대, 일정 표|date/account × probability; due_date × amount|
|optimize|가능/불가 후보 scatter 및 순위 표|expected_saving × joint_success; candidate_id|

`visualizations`는 renderer 독립적인 선언형 명세이다. dataset 이름·field·unit·null 처리,
분위수 의미를 포함한다. 숫자를 AI가 다시 계산할 필요가 없다.
`scripts/render_report.py`는 정형 output을 검토하는 오프라인 HTML/SVG 샘플 어댑터이며
핵심 엔진이나 사용자 앱이 아니다. 추천 문장/자연어 응답을 생성하지 않는다.

## 7. 이벤트와 영속화

수동 `update`는 source=LIVE 거래 추가, 전체 취소, 권위 스냅샷 교체를 받는다.
모든 이벤트는 event_id/user_id를 요구한다. 동일 이벤트 재처리는 no-op,
동일 ID의 다른 내용은 충돌. batch는 전부 성공 또는 전부 롤백한다.
거래 추가만으로 관측 잔액을 만들어내지 않는다. 거래·취소가 들어오면 같은 날짜라도 snapshot_dirty를 표시한다. 해당 변경 이후
snapshot 갱신이 없으면
절대 잔액 계산을 중단한다. 신규 SEED는 초기 재생성으로만 받는다.
SQLite 단일 사용자 DB에 revision CAS + transaction commit으로 보관한다.
절대 상태는 같은 날짜의 거래라도 snapshot_dirty이면 사용하지 않는다.
최적화/what-if/run은 읽기 전용. 외부 은행 호출은 코드에 없다.

## 8. 테스트/수용 기준

- A01: 4 CSV 모두 생성, 980건 reconciliation, 혼합 사용자 거부.
- A02: 5모드×4명 = 20 실행; schema·numeric invariant·visualization 참조 검사.
- A03: history-only의 절대 잔액/목표 unknown 보존.
- A04: 동일 입력·seed·환경 재현, what-if 0변경 동일, 원본 비변경.
- A05: 카드 승인/청구 중복 방지, 내부이체 보존, ATM/저축 소비 제외.
- A06: 목표·최적화 실패/불가를 정상적인 구조화 결과로 반환.
- A07: 사건 멱등/충돌/rollback/CAS, snapshot 날짜 누락 보호.
- A08: 시간 절단 backtest(미래 누수 방지), 실제 오차/구간 포함 여부 보고.
- A09: 금액 경계·0수입·빈입력·한글 BOM·월말·유효성 거부 테스트.
- A10: CLI부터 ZIP 재설치 smoke까지 재실행; 실행 환경/소요시간 기록.

## 9. 한계와 다음 확장 지점

v0.1은 **이벤트 갱신 가능한 개인 현금흐름 중심 Twin**이다. 전체 재산을 복원하는
검증된 디지털 복제나 실제 금융 실행 시스템이 아니다. 투자/부채 상태는 입력받아 표시하고
명시적 자산 shock은 계산하지만 수익률·상환 스케줄을 임의 학습하지 않는다.
향후 loan amortization, portfolio covariance, insurance coverage, 휴일 달력, 실시간 API
adapter, 장기 행동/인과 모델은 별도 모듈·데이터·검증이 필요하다.
백테스트는 합성 데이터 내부 점검이며 실제 사용자에서의 보정/효과 검증이 아니다.

기술 참고(첨부 사업 요구사항과 구별):
- NumPy RNG 재현성: https://numpy.org/doc/2.2/reference/random/compatibility.html
- Python SQLite transaction: https://docs.python.org/3/library/sqlite3.html
- JSON Schema types: https://json-schema.org/understanding-json-schema/reference/type
