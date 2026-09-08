# Engine / 외부 Agent 계약

## 1. 외부 시스템과의 경계

```text
외부 수집기 ─ CSV 또는 LIVE 이벤트 + 권위 snapshot ─→ Twin
외부 Agent ─ validated JSON request ────────────────→ Engine.run
외부 Agent ← metrics + datasets + ChartSpec + caveats ← Engine.run
```

외부 Agent는 질문 의도와 파라미터만 결정합니다. 계산·확률·분기 비교·최적화는 엔진이 합니다.
엔진 결과에서 숫자를 다시 '추론'하거나 null을 0으로 채우면 안 됩니다.
질문이 모호하면 외부 Agent가 target/date/잔액/시나리오 가정을 사용자에게 확인해야 합니다.
이 저장소에는 자연어 Agent 구현이 없습니다.

## 2. 요청 파라미터

공통: `mode`, `horizon_days=90`, `paths=400`, `seed=42`.
`mode`는 5개 enum만 가능. request의 알 수 없는 필드나 잘못된 mode 전용 필드는 거부합니다.

### What-if 예시

```json
{
  "mode": "what_if",
  "horizon_days": 60,
  "paths": 400,
  "seed": 42,
  "scenario": {
    "expense_reductions": {"외식": 0.2},
    "income_multiplier": 0.9,
    "cash_events": [
      {"date": "2026-09-10", "account_id": "ACC-DEMO-002", "amount_krw": 500000, "direction": "EXPENSE"}
    ]
  }
}
```

cash_events 날짜는 현재 Twin 이후이면서 horizon 안이어야 합니다.
감축은 7봉투 중 지정한 **비고정·비반복 소비**에만 적용됩니다.
`expense_multiplier`는 `expense` 흐름 전체(보호된 반복 소비 포함)에 적용하고 `fixed_expense`에는 적용하지 않습니다. 원리금·저축·인출에도 적용하지 않습니다.
`cancel_rule_ids`는 inspect의 recurring_rules에서 선택한 알려진 일정에만 적용합니다.
`asset_shock_fraction`은 제공한 투자 평가액의 정적 충격이며 현금 유입이 아닙니다.

### 고정지출 시나리오 필드

|필드|적용 범위와 의미|
|---|---|
|`expense_reductions`|비보호 `expense` 잔여 소비만 감축합니다. 반복·수동 일정과 `fixed_expense`는 제외합니다.|
|`expense_multiplier`|`expense` 흐름 전체(보호 소비·PENDING 포함)에 적용하고 `fixed_expense`는 제외합니다.|
|`fixed_multiplier`|기본값 1, 범위 0~5. 고정지출 잔여 거래와 고정지출 규칙에 적용합니다.|
|`fixed_overrides`|`[{rule_id, amount_krw}]`, 최대 100개. 현재 `fixed_expense` 규칙의 발생 기준 금액을 배율 전에 교체합니다.|
|`cancel_rule_ids`|알려진 규칙을 취소합니다. 같은 ID를 `fixed_overrides`와 함께 지정하면 거부합니다.|
|`cash_events[].fixed_group`|`EXPENSE`에만 지정할 수 있습니다. 지정하면 고정지출 그룹에 넣고 `fixed_multiplier`는 적용하지 않습니다. `INCOME`에 지정하면 `SCENARIO_FIXED_GROUP_INCOME`입니다.|

처리 순서는 규칙 ID 검증 → 취소 → `fixed_overrides` → 종류별 배율 → 집계입니다. `fixed_overrides`의 금액은 0~10^12 KRW 범위를 지켜야 하며, 미등록·비고정 규칙과 중복 override는 오류입니다.

### PENDING 처리

`confirm_status=PENDING` 거래는 `pending=true`인 `expense`로 남깁니다. 현금 소비(`consumption`)와 잔액 흐름에는 포함하지만 `envelope`, `budget_by_envelope`, 봉투 지표에서는 제외합니다. 고정 그룹을 부여하지 않으며 원 `category`·`subcategory`만 보존합니다.

관측 합계는 `pending_consumption_krw`, 예측 합계는 `pending_expense_p50_krw`로 별도 제공합니다. 관측 소비 중 PENDING 금액 비율이 5%를 넘으면 `PENDING_SHARE_HIGH` 경고(details: `share`, `rows`, `krw`)를 내고 결과 `status`를 `partial`로 낮춥니다.

### Goal 예시

```json
{"mode":"goal", "horizon_days":90, "goal":{"target_krw":3000000,"reserve_krw":200000,"success_probability":0.8}}
```

목표는 '90일 뒤 현재 보유 현금을 포함한 가용 현금'입니다.
`cash − card_payable − reserve ≥ target`을 계산합니다.
'현재보다 300만원 더 모으기'라면 외부 Agent가 현재 가용 자금을 확인한 후
그 값+300만원을 target으로 명시해야 합니다. 미상 상태에서 변환하지 않습니다.
투자/부동산/잠긴 적금의 자동 처분을 가정하지 않습니다.

`p_goal_reached`와 `p_goal_and_no_shortfall`은 다릅니다. 뒤의 값은 개별 계좌가
기간 중 한 번도 음수가 되지 않아야 합니다. 실제 금융기관의 연체 판정은 아닙니다.

추가 외부 수입 등가액은 기간 내 1/31/61/…일의 동일액 유입을 가정한 **만기** 부족분 계산입니다.
초기 부족·타 계좌 부족을 해결한다는 보장은 없으며, 저축액 자동 생성이나 수입 권고가 아닙니다.
Monte Carlo Wilson 구간은 샘플링 오차만 다룹니다. 모델 오류를 포함하는 보정 구간이 아닙니다.

### Optimize 예시

`examples/requests/optimize.json`을 사용합니다. 기본 27개 후보를 모두 평가하며
의료·주거비를 마음대로 줄이거나 신규 대출/투자 상품을 추천하지 않습니다.
유한 후보 내 목적함수는 최소 기대 소비 감축액입니다. 효용 함수의 일반해가 아닙니다.
`minimum_remaining_monthly_krw`는 후보별 잔여 **변동 소비**의 30.4375일 환산 평균 하한입니다.
예: 외식 최소 15만원. 계약상 의무 비용의 실현 가능성을 대신 검증하지는 않습니다.

## 3. 지표·데이터셋·시각화

### 3-1. 새 지표와 데이터셋

공통 결과에는 소비(C)와 고정지출(F)을 분리한 지표를 함께 제공합니다.

|지표|의미·단위|basis|
|---|---|---|
|`total_expense_p10/p50/p90_krw`, `expected_expense_krw`|소비 C의 경로 분위수·기대값|`simulation_consumption_only`|
|`total_fixed_p10/p50/p90_krw`, `expected_fixed_krw`|고정지출 F의 경로 분위수·기대값|`simulation_fixed_only`|
|`fixed_monthly_p50_krw`|P50(F) × 30.4375 / 기간 일수|KRW|
|`fixed_share_of_outflow`|mean(F) / mean(C+F)|`ratio`|
|`total_outflow_p50_krw`|경로별 C+F 합의 P50|KRW|
|`pending_expense_p50_krw`|PENDING 소비 합의 P50|KRW|
|`pending_consumption_krw`|관측 PENDING 소비 합계|`observed`|
|`fixed_coverage_months`|risk 모드에서 가용 잔액 / 월 고정지출|`months`|

`datasets.fixed_groups`는 `group`, `p10_krw`, `p50_krw`, `p90_krw`를 가진 6행을 항상 제공합니다. what-if에는 분기 결과를 같은 형식의 `branch_fixed_groups`로 제공합니다. 일별 데이터에는 `cumulative_fixed_p50_krw`와 `cumulative_pending_p50_krw`를 추가합니다. `envelopes` 데이터셋은 고정지출을 제외한 변동 소비만 담습니다.

### 3-2. 시각화 바인딩

`result.visualizations` 예시 구조:

```json
{
  "id":"projection",
  "kind":"band_line",
  "dataset":"projection",
  "x":"date",
  "y":["cash_balance_p50_krw"],
  "lower":"cash_balance_p10_krw",
  "upper":"cash_balance_p90_krw",
  "unit":"KRW",
  "null_policy":"gap"
}
```

실제 명세에는 title/note도 포함됩니다. 모든 행은 `result.datasets[dataset]`에서 가져옵니다.
P10/P90 band는 확정된 상·하한이 아닙니다. 중앙선은 경로 중앙값입니다.
프런트엔드가 KRW를 만원으로 표시하려면 축 표시만 변환하고 원본 값은 유지하세요.
probability는 0~1, 화면에서만 ×100% 변환합니다. null은 선을 끊고 부족한 입력을 표시합니다.
`candidates` scatter의 x는 KRW, y는 probability. feasible/selected를 형태/범례로 구분합니다.

UI/Agent 구현 시, `status`, `model.calibrated`, `assumptions`, `warnings`를 숨기지 마세요.
각 metric의 `basis`, `method`, `evidence`를 설명 또는 상세보기에서 연결하세요.
`input_digest`와 twin revision으로 어떤 입력 상태를 계산했는지 확인할 수 있습니다.
원본 파일 해시/행 근거는 Twin의 metadata/transaction.origin에 있고, inspect는 원문 개인정보를 노출하지 않습니다.

## 4. 권위 스냅샷과 이벤트

스냅샷은 기준일 마감 상태입니다. source는 `USER_ASSUMPTION` 또는 `LIVE`.
LIVE는 **호출자가 관측이라고 선언한 태그**입니다. 엔진이 금융망 진위를 인증하지는 않습니다.

예제 `examples/events_001.json`은 신규 거래 후 새 마감 잔액을 넣는 batch입니다.
멱등 ID와 사용자 ID를 검증합니다. 전체 성공/전체 롤백이며 동일 날짜의 신규 관측도 snapshot을 stale로 만듭니다.
다른 계좌로 보내는 본인 이체는 명시적 to_account_id가 필요합니다.

API 연동 시 참고한 첨부 문서:

|첨부 경로 / 절|관측 필드 또는 의미|엔진 경계|
|---|---|---|
|금융_api/api/api-demand-deposit.md §2.4.7|accountBalance|snapshot.accounts[].balance_krw에 확인된 마감 잔액|
|금융_api/api/api-credit-card.md §2.8.10|estimatedBalance|미청구/미결제 상태 별도 대사 필요, 소비 CSV 합계로 역산 금지|
|같은 문서 §2.8.12|billingDate, totalBalance, status|실제 납부일을 별도 확인하여 known_bills 구성|
|같은 문서 §2.8.13|출금 연결계좌와 출금 날짜 변경|실제 기관 정책 adapter 필요; 엔진은 실행하지 않음|
|금융_api/api/api-recurring-payment.md §2.18.3|nextPaymentDate, billingCycle|수동 schedules로 알려진 일정 전달 가능|
|금융_api/api/api-loan.md §2.7.8~9|loanBalance|총 대출/원리금 등 표현이 달라 잔여 원금 증명 없이 principal로 복사 금지|

현재 demo 신용카드 정책은 `다음 월요일 발행 + payment_delay_days`입니다.
실제 금융망의 임의 출금일·할인·공휴일을 모두 재현하는 API adapter가 아닙니다.
실제 미래 청구 모델 연결 시 그 정책을 별도로 확장해야 합니다.

## 5. 오류와 동시성

오류 시 CLI exit code 2 + `{status:"error", error:{code,message,details}}`.
라이브러리는 `FDTError`를 발생시키며 `as_dict()`로 같은 구조를 받습니다.
주요 code: SCHEMA_VALIDATION, MIXED_OR_EMPTY_USER, TRANSACTION_CONFLICT,
EVENT_CONFLICT, REVISION_CONFLICT, OPENING_PAYABLE_MISMATCH, SIMULATION_LIMIT,
MONEY_RANGE_LIMIT, OPTIMIZATION_LIMIT.

고정지출 분리와 입력 계약에서 추가되는 오류 code는 다음과 같습니다.
`INCONSISTENT_RECORD`, `SCHEDULE_FIXED_GROUP_REQUIRED`, `SCHEDULE_ENVELOPE_FORBIDDEN`,
`SCHEDULE_FIXED_GROUP_FORBIDDEN`, `UNKNOWN_TRANSACTION_REPLACEMENT`,
`DUPLICATE_TRANSACTION_REPLACEMENT`, `REPLACEMENT_ALREADY_RULED`,
`REPLACEMENT_CHANNEL_MISMATCH`, `REPLACEMENT_CONFLICT`, `OVERRIDE_NOT_FIXED`,
`DUPLICATE_OVERRIDE`, `OVERRIDE_CANCEL_CONFLICT`, `SCENARIO_FIXED_GROUP_INCOME`,
`FLOW_INVARIANT`.

추가 경고 code는 `IGNORED_LABEL_COLUMNS`, `FIXED_UNSCHEDULED`, `PENDING_SHARE_HIGH`입니다.
`IGNORED_LABEL_COLUMNS`는 무시한 입력 열이 있을 때, `FIXED_UNSCHEDULED`는 규칙에 배정되지 않은 고정지출 잔여 거래가 있을 때 발생합니다.

CLI는 SQLite 단일 Twin DB이며 사용자별 DB 또는 상위 애플리케이션의 분리 저장이 필요합니다.
분석은 읽기 전용. update에는 expected_revision을 넣고 충돌 시 최신 상태를 다시 읽습니다.
DB 평문/인증 부재는 개발용 선택입니다. 운영 환경에서는 보안·접근통제·암호화가 필요합니다.

과거 cutoff 재학습은 정적인 CSV 입력 대상으로 검증했습니다.
현재 DB의 취소 상태를 과거 시점으로 복원하는 bitemporal time-travel 저장소는 구현하지 않았습니다.
