# 구현 계약: 고정지출 분리 + 입력 계약 (병렬 구현용)

이 문서는 `DESIGN_FIXED_EXPENSE_SEPARATION.md`를 여러 작업자가 동시에 구현할 때 이름과 형식을 맞추기 위한 고정 계약이다. 설계 문서와 다르게 보이는 곳은 이 문서가 우선한다. 여기 없는 판단은 설계 문서를 따른다.

## 0. 작업 분할과 파일 소유권

| 작업 | 소유 파일 (이 파일만 수정) | 실행할 테스트 |
|---|---|---|
| W1a ingest | `fdt/mapping.py`, `fdt/ingest.py`, `tests/conftest.py`, `tests/test_ingest.py` | `tests/test_ingest.py` |
| W1b schemas+docs | `scripts/make_schemas.py`, `fdt/schemas/request.json`, `fdt/schemas/snapshot.json`, `fdt/schemas/result.json`, `docs/CATEGORY_MAPPING.md`, `docs/INTEGRATION.md` | `python scripts/make_schemas.py` 후 JSON 유효성만 |
| W1c ui | `workbench/app.py`, `workbench/static/app.js`, `workbench/static/charts.js`, `workbench/static/index.html`, `workbench/static/app.css`, `tests_web/test_api.py` | `tests_web/test_api.py` 중 config·schema 관련만 |
| W2a model+store | `fdt/model.py`, `fdt/store.py`, `fdt/cli.py`, `tests/test_model.py`, `tests/test_store.py`, `tests/test_cli.py` | 좌측 테스트 |
| W2b sim+engine | `fdt/simulation.py`, `fdt/engine.py`, `tests/test_simulation.py`, `tests/test_modes.py`, `tests/test_renderer.py` | 좌측 테스트 |
| W3 examples | `scripts/create_examples.py`, `scripts/backtest.py`, `examples/**`, `README.md`, `QA_REPORT.md` | 전체 |

다른 작업의 파일은 읽기만 한다. 다른 작업의 파일이 아직 바뀌지 않아 내 테스트가 실패하면, 그 테스트를 `pytest.mark.skip`하지 말고 계약대로 작성해 두고 보고서에 "의존 작업 대기"로 표시한다. 전체 스위트가 아니라 소유 테스트만 실행한다.

## 1. 상수와 버전

```python
# fdt/mapping.py
MAPPING_VERSION = 'keyfin-csv-map/2.0'
ENVELOPES = ('외식', '교통비', '의료·건강', '취미·여가', '쇼핑', '편의점·마트·잡화', '기타')   # 불변
FIXED_GROUPS = ('주거', '공과금', '통신', '보험·사회보험', '세금', '구독·멤버십')
FIXED_LOOKUP = {
    '월세': '주거', '관리비': '주거',
    '전기요금': '공과금', '가스요금': '공과금', '수도요금': '공과금',
    '통신': '통신', '인터넷': '통신',
    '실손보험': '보험·사회보험', '사회보험': '보험·사회보험',
    '자동차세': '세금',
    '구독': '구독·멤버십', '코워킹': '구독·멤버십',
}
def fixed_group(subcategory: str) -> str | None   # 정확 매칭만, 없으면 None

# fdt/model.py
MODEL_VERSION = 'calendar-block-bootstrap/2.0'
```

## 2. CSV 입력 계약 (fdt/ingest.py)

```python
REQUIRED = {'user_id','transaction_id','source','transaction_type','transaction_date','transaction_time',
            'category','subcategory','merchant','merchant_id','amount_krw','account_id','card_id',
            'confirm_status','status'}
OPTIONAL = {'exclude_tag': 'NONE', 'direction': None, 'payment_method': None, 'to_account_id': None}
IGNORED  = ('is_fixed', 'is_recurring', 'spend_pattern', 'classify_source')
```

- `REQUIRED`가 없으면 `MISSING_COLUMNS`. `OPTIONAL`은 없으면 기본값. `IGNORED`는 있어도 읽지 않는다.
- `direction`, `payment_method`가 있으면 `transaction_type`·`card_id`와 일치하는지만 검증한다. 불일치 시 `INCONSISTENT_RECORD`. 없으면 도출한다.
- 채널 판정: `card_id`가 비어 있지 않으면 카드 채널, 아니면 `account_id` 필수(`MISSING_CHANNEL`). `transaction_type`이 `CARD`이면 `card_id` 필수.
- `load_csv`가 반환하는 meta에 `ignored_columns: list[str]`(실제로 존재해 무시한 열, 정렬)를 넣는다.
- `ENUMS`에서 `direction`, `payment_method`, `spend_pattern`은 선택 검증으로 바꾸고 나머지는 유지한다.

### 종류(kind) 판정 순서

1. `transaction_type in ('CARD_BILL','CARD_SETTLEMENT')` → `card_settlement`
2. `subcategory == 'ATM 출금'` → `cash_withdrawal`
3. `subcategory == '대출 상환'` → `debt_service`
4. `exclude_tag in ('INTERNAL_TRANSFER','SELF_TRANSFER')`이거나, `direction == 'TRANSFER'`(열이 있을 때)이거나, `transaction_type in ('TRANSFER','TRANSFER_OUT')`이면서 `category == '저축·투자'` → `category == '저축·투자'`면 `savings_out`, 아니면 `internal_transfer`.
   **주의(2026-09-08 정정)**: `TRANSFER_OUT` 자체는 내부 이체의 근거가 아니다. 축의금·회비·모임 정산 송금이 `TRANSFER_OUT`으로 기록되며 이는 소비다. 내 계좌 간 이동은 사용자 태그(금융망에는 없는 정보)로만 식별한다. `direction` 열이 없을 때 `TRANSFER_OUT`의 도출 방향은 `EXPENSE`, `TRANSFER` 타입만 `TRANSFER`다.
5. 입금(`DEPOSIT`, `TRANSFER_IN`으로 판정된 수입) → `subcategory == '모임 정산'`이면 `reimbursement`, 아니면 `income`
6. 남은 지출 중 `confirm_status != 'PENDING'`이고 `fixed_group(subcategory)`가 있으면 → **`fixed_expense`**
7. 그 외 지출 → `expense`

현재 코드의 `direction` 기반 판정을 `transaction_type` 기반으로 옮기되, 데모 CSV 4개의 판정 결과(kind 분포)는 `fixed_expense` 신설 외에는 바뀌지 않아야 한다. `tests/test_ingest.py::test_actual_inputs`의 행 수 337/193/262/188은 유지.

### Transaction 필드

`Transaction`에 아래 두 필드를 `subcategory` 바로 뒤에 추가한다(위치 인수 순서 주의).

```python
fixed_group: str | None    # kind == 'fixed_expense'일 때만 값
pending: bool              # kind == 'expense' and confirm_status == 'PENDING'
```

- `fixed_expense`: `envelope=None`, `subcategory=None`, `budget_amount_krw=0`, `mapping_fallback=False`, `fixed_group` 필수.
- `pending` expense: `envelope=None`, `subcategory=None`, `budget_amount_krw=0`, `mapping_fallback=False`. 분류는 `raw_category`/`raw_subcategory`에만 남는다.
- 그 외 필드 의미 불변.

### 서명 두 종류

```python
def transaction_signature(t) -> str   # 기존. origin/raw 제외 전 필드. 동일 ID 충돌 판정용
def record_signature(t) -> str        # 신규. 기록 필드만: id, user_id, date, time, source, amount_krw,
                                      #   account_id, card_id, to_account_id, merchant_id, raw['transaction_type'], active
```

`record_signature`가 같고 `transaction_signature`만 다른 두 행은 "분류만 바뀐 갱신"이다(store가 사용).

### audit 추가 키

```python
'fixed_totals_krw': {group: 합계}                 # 6그룹 모두 키 존재, 없으면 0
'fixed_raw_subcategory_totals_krw': {raw_sub: 합계}
'pending_consumption_krw': int                     # pending expense 합계
'pending_rows': int
```

기존 `envelope_totals_krw`, `raw_*_totals_krw`, `mapping_quality`는 `kind=='expense' and not pending`만 대상으로 한다. 검산 `sum(envelope_totals) == kind_totals['expense'] - pending_consumption_krw`.

audit 경고 코드: 기존 유지 + `PENDING_CLASSIFICATION`(건수·금액 포함). `FIXED_UNSCHEDULED`와 `IGNORED_LABEL_COLUMNS`는 model/engine이 붙인다.

## 3. 흐름 채널과 규칙 (fdt/model.py)

```python
def flow(t) -> dict:
    return {'kind': t.kind, 'envelope': t.envelope, 'fixed_group': t.fixed_group, 'pending': t.pending,
            'account_id': t.account_id, 'card_id': t.card_id, 'to_account_id': t.to_account_id,
            'protected': t.kind == 'fixed_expense',      # 라벨 보호 삭제
            'budgeted': t.exclude_tag == 'NONE' and t.kind == 'expense' and not t.pending}
```

- `_fit`: 그룹 키에서 `is_recurring` 제거. 반복 인정은 항상 관측 날짜 3개 이상. 규칙 dict에 `'kind'`, `'fixed_group'` 추가. 규칙에 잡힌 흐름은 `protected=True`(기존 동작).
- 수동 일정(`snapshot.schedules`)의 flow도 위 키를 모두 가진다. `kind=='fixed_expense'`면 `fixed_group` 필수, `budgeted=False`.
- `FIXED_UNSCHEDULED`: 규칙에 배정되지 않은 `fixed_expense` 잔여 거래가 있으면 `self.model['audit']['warnings']`에 추가. details: `items=[{raw_subcategory, fixed_group, count, total_krw, last_date, last_amount_krw, transaction_ids}]`.
- `snapshot.schedules[].replaces_transaction_ids`: 활성 fixed_expense 잔여 거래 ID만 허용. 그룹·계좌·카드 일치 검사. 오류 코드 `UNKNOWN_TRANSACTION_REPLACEMENT`, `DUPLICATE_TRANSACTION_REPLACEMENT`, `REPLACEMENT_ALREADY_RULED`, `REPLACEMENT_CHANNEL_MISMATCH`, `replaces_rule_id`와 동시 사용 시 `REPLACEMENT_CONFLICT`. 지정 거래는 이력에 남고 미래 일별 표에서만 제외.
- `validate_snapshot`: `kind=='fixed_expense'`는 `fixed_group` 필수·`envelope` 금지(`SCHEDULE_FIXED_GROUP_REQUIRED`, `SCHEDULE_ENVELOPE_FORBIDDEN`). 다른 kind에 `fixed_group`이 있으면 `SCHEDULE_FIXED_GROUP_FORBIDDEN`. 카드 일정 허용 kind는 `expense`, `fixed_expense`.
- `Twin.metadata['ignored_columns']`는 `from_csv`가 load_csv meta에서 복사.
- `inspect()` 추가: `behavior.fixed_monthly_observed_krw`, `behavior.fixed_groups_observed_krw`(6키), `behavior.pending_consumption_krw`, `recurring_rules[].kind/fixed_group`, `merchant_classifications: [{merchant_id, subcategory, envelope, fixed_group, confirmed_count, last_date}]`(CONFIRMED 행만 집계). `behavior.impulse_*` 삭제.
- `relationships()`: fixed_expense 거래에 `(t.id, 'fixed-group:'+group, 'classified_as')` 관계와 노드 `{'id':'fixed-group:주거','type':'fixed_group'}` 추가.

## 4. 저장소 (fdt/store.py)

`apply_events`의 transaction 이벤트에서 같은 ID가 이미 있을 때:

- `transaction_signature` 동일 → 기존처럼 무시.
- `record_signature` 동일, `transaction_signature` 상이 → **분류 갱신**으로 받아 교체. `snapshot_dirty`는 세우지 않는다. `metadata['reclassified_count']` 증가.
- `record_signature` 상이 → `TRANSACTION_CONFLICT`(기존).

## 5. 시뮬레이션 (fdt/simulation.py)

`Simulation`에 추가(위치 인수 순서: 기존 필드 뒤, `account_ids` 앞에 넣지 말고 **맨 뒤 `scenario` 앞**에 추가):

```python
fixed: np.ndarray            # paths x days
fixed_by_group: np.ndarray   # paths x days x len(FIXED_GROUPS)
pending: np.ndarray          # paths x days, pending expense 합계 (consumption에도 포함됨)
```

- `consumption`에는 `expense`(pending 포함)만. `by_envelope`, `budget_by_envelope`에는 `expense and not pending`만. `fixed`에는 `fixed_expense`만.
- 자원·현금 차감은 기존 포괄 규칙 그대로(`fixed_expense`는 자동으로 차감 대상). 별도 차감 추가 금지.
- 카드 흐름 검사: `if kind not in ('expense','fixed_expense'): raise UNSUPPORTED_CARD_FLOW`.
- 시나리오 적용 순서: rule_id 검증 → `cancel_rule_ids` → `fixed_overrides` → 종류별 배율 → 집계.
  - `expense_reductions`: 비보호 `expense`만(기존).
  - `expense_multiplier`: `expense` 전체(보호 포함), `fixed_expense` 제외.
  - `fixed_multiplier`(기본 1): `fixed_expense` 전체(잔여 + 규칙).
  - `fixed_overrides: [{rule_id, amount_krw}]`: 해당 규칙의 각 발생 금액을 교체. 오류 `UNKNOWN_RULE`(기존), `OVERRIDE_NOT_FIXED`, `DUPLICATE_OVERRIDE`, `OVERRIDE_CANCEL_CONFLICT`.
  - `cash_events[].fixed_group`: `direction=='EXPENSE'`에서만 허용(`SCENARIO_FIXED_GROUP_INCOME` 오류). 있으면 `fixed`/`fixed_by_group`에, 없으면 기존처럼 `기타` 봉투에 집계. 배율은 적용하지 않는다(기존 cash_events 의미 유지).
- 캘린더 행에 `fixed_group` 키 추가(해당 없으면 `None`). 반복 이벤트 이름은 기존 규칙 그대로 `'RECURRING_'+kind.upper()` → `RECURRING_FIXED_EXPENSE`. fixed_group 있는 cash_event는 `SCENARIO_FIXED_EXPENSE`. `expected_amount_krw`는 변환 후 표본 평균으로 계산(배율 이중 적용 금지).
- 불변식 추가: `fixed == fixed_by_group.sum(axis=2)`, 그리고 지출 항 검산(`FLOW_INVARIANT`).
- `daily_rows`에 `cumulative_fixed_p50_krw`, `cumulative_pending_p50_krw` 추가.

## 6. 엔진 (fdt/engine.py)

공통 지표(`_base`):

| 키 | 값 |
|---|---|
| `total_expense_p10/p50/p90_krw`, `expected_expense_krw` | 소비(C). basis `simulation_consumption_only` |
| `total_fixed_p10/p50/p90_krw`, `expected_fixed_krw` | 고정지출(F). basis `simulation_fixed_only` |
| `fixed_monthly_p50_krw` | P50(F) × 30.4375 / H |
| `fixed_share_of_outflow` | mean(F)/mean(C+F), unit `ratio`, 분모 0이면 None |
| `total_outflow_p50_krw` | P50(C+F) 경로별 합 후 분위수 |
| `pending_expense_p50_krw` | P50(pending 합) |
| `pending_consumption_krw` | 관측 pending 합(audit에서), basis `observed` |

데이터셋 `fixed_groups`: `[{group, p10_krw, p50_krw, p90_krw}]` 6행 항상. 시각화 `fixed_groups` bar 제목 "고정지출 그룹별 예상", `envelopes` bar 제목 "봉투별 예상 소비 (고정지출 제외)".

경고·상태:
- `IGNORED_LABEL_COLUMNS`: `twin.metadata.get('ignored_columns')`가 비어 있지 않으면 1회.
- `PENDING_SHARE_HIGH`: 관측 소비 중 pending 금액 비율 > 0.05이면 경고(details `share`, `rows`, `krw`)하고 `status`를 `partial`로 낮춘다(`insufficient_data`보다 우선순위 낮음).
- `FIXED_UNSCHEDULED`는 audit에서 전달됨(기존 warnings 복사 경로).

모드별:
- what_if: `paired_fixed_delta_p10/p50/p90_krw` = branch F − base F. `branch_fixed_groups` 데이터셋.
- goal/optimize: `decision['fixed_monthly_p50_krw']`.
- risk: `fixed_coverage_months` = max(기초 free, 0) / fixed_monthly_p50_krw, unit `months`, 분모 0 또는 잔액 미확정이면 None. 기본 stress에 `{'name':'고정지출 10% 인상 가정','fixed_multiplier':1.1}` 추가. `_budget_risk` 관측 사용액에 `t.kind=='expense' and not t.pending` 조건 명시.
- optimize: 후보 하한 계산 조건 `f['kind']=='expense' and not f['pending'] and f['envelope']==env and not f['protected']`. 감축 대상 봉투 enum 불변.
- `_merge_scenario`: `expense_reductions`만 키 병합(기존). `fixed_overrides`, `cancel_rule_ids`는 배열 통째 교체. 병합 후 override/cancel 충돌 재검사.

`LIMITATIONS`에 한 줄 추가: "고정지출은 세부분류 대응표로 판정한 종류이며 계약상 의무나 해지 불가를 의미하지 않습니다. 미확정(PENDING) 소비는 잔액에는 포함되고 봉투 통계에서는 제외됩니다."

## 7. 스키마 (scripts/make_schemas.py → fdt/schemas/*.json)

- request: `scenario.fixed_multiplier` number 0~5, `scenario.fixed_overrides` arr(obj{rule_id STR, amount_krw MONEY}, 100), `scenario.cash_events[].fixed_group` enum(FIXED_GROUPS). `stress_scenarios[]` 항목에도 동일 세 필드.
- snapshot: `schedules[].kind` enum에 `fixed_expense` 추가, `schedules[].fixed_group` enum(FIXED_GROUPS), `schedules[].replaces_transaction_ids` arr(STR, 100).
- result: `metrics.*.unit` enum에 `months` 추가. `status` enum은 기존(`ok`, `partial`, `insufficient_data`) 유지.
- `ENVS` 옆에 `FIXED = [...]` 리스트 추가. 생성 후 세 JSON을 커밋 상태로 재생성.

## 8. 웹 (workbench)

- `GET /api/config`에 `fixed_groups: FIXED_GROUPS` 추가. `/api/health`의 `engine_version`을 `'0.2.0'`으로.
- `app.js`: what_if·risk 시나리오 폼에 고정지출 배율(`fixed_multiplier`, % 스케일), 규칙 금액 교체 편집기(`fixed_overrides`: inspect의 `recurring_rules` 중 `fixed_group`이 있는 항목 드롭다운 + 금액), cash_event에 고정 그룹 선택. stress 편집기에 `fixed_multiplier`. `METRIC_NAMES`에 새 지표 한글명. 모드별 주요 카드에 `total_fixed_p50_krw`(forecast), `paired_fixed_delta_p50_krw`(what_if), `fixed_coverage_months`(risk). `metricValue`가 unit `months`를 "개월"로 표시. 활성 Twin 요약에 관측 고정지출 월 합계와 그룹별 표, `FIXED_UNSCHEDULED`·`PENDING_SHARE_HIGH` 경고의 details 표시.
- `charts.js`: 변경 없거나 bar 렌더러가 `fixed_groups` 데이터셋의 `group` x축을 처리하는지 확인.
- `tests_web/test_api.py`: config에 `fixed_groups` 6개, 봉투 7개 유지, 스키마가 `fixed_overrides`를 검증.

## 9. 예제와 문서 (W3)

- `scripts/create_examples.py`: 기존 식(봉투 관측 × 30.4375 / 관측일 × 1.1, 천 원 반올림)으로 4개 스냅샷 재생성. `examples/requests/risk.json`의 stress 배열에 고정지출 10% 인상 추가. `what_if.json`은 유지.
- `scripts/backtest.py`: fixed 관측·예측 오차 추가.
- `docs/CATEGORY_MAPPING.md`: 종류·고정 그룹 열 추가, "미등록은 기타" 문장을 상위 카테고리 fallback으로 정정, 입력 계약(필수·선택·무시 열) 절 추가.
- `docs/INTEGRATION.md`: 배율 의미, fixed_overrides, PENDING 처리, 새 지표.

## 10. 오류 코드 총람 (신규)

`INCONSISTENT_RECORD`, `SCHEDULE_FIXED_GROUP_REQUIRED`, `SCHEDULE_ENVELOPE_FORBIDDEN`, `SCHEDULE_FIXED_GROUP_FORBIDDEN`, `UNKNOWN_TRANSACTION_REPLACEMENT`, `DUPLICATE_TRANSACTION_REPLACEMENT`, `REPLACEMENT_ALREADY_RULED`, `REPLACEMENT_CHANNEL_MISMATCH`, `REPLACEMENT_CONFLICT`, `OVERRIDE_NOT_FIXED`, `DUPLICATE_OVERRIDE`, `OVERRIDE_CANCEL_CONFLICT`, `SCENARIO_FIXED_GROUP_INCOME`, `FLOW_INVARIANT`.

경고 코드(신규): `IGNORED_LABEL_COLUMNS`, `FIXED_UNSCHEDULED`, `PENDING_SHARE_HIGH`.
