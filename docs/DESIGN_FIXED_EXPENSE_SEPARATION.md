# 설계: 고정지출을 소비 봉투에서 분리한다

상태: 실제 코드 대조를 거친 설계 초안. 아래 변경은 아직 구현하지 않았다.
작성·검토일: 2026-09-08
제안 버전: 엔진 0.1 → 0.2, 매핑 `keyfin-csv-map/1.1` → `2.0`, 모델 `calendar-block-bootstrap/1.0` → `2.0`. Workbench는 이미 0.2.0이므로 엔진 버전과 구분한다.

## 0. 요약과 검토 근거

월세, 관리비, 공과금, 통신, 보험, 구독을 별도 종류 `fixed_expense`로 분리한다. 7개 소비 봉투의 이름과 개수는 유지한다. 고정지출도 자금 여력을 줄이고 계좌·카드 흐름에 반영되지만 소비 봉투와 소비 감축률에서는 제외한다.

consumer_001의 현재 기타 합계 2,930,200원 중 분리 대상은 2,643,700원(약 90.2%)이고 남는 소비는 286,500원이다. 단, 봉투에 남는 소비가 모두 감축 가능한 것은 아니다. 의료비, 교육비, 라벨이나 반복 규칙으로 보호되는 소비도 봉투에 남는다.

검토 근거는 다음 로컬 파일의 현재 구현이다. 문서와 코드가 다를 때는 코드의 실제 분기를 기준으로 했다.

- `fdt/mapping.py`, `ingest.py`, `model.py`, `simulation.py`, `engine.py`, `store.py`, `cli.py`.
- `fdt/schemas/request.json`, `snapshot.json`, 추가 확인한 `result.json`.
- `workbench/app.py`, `workbench/static/app.js`, `charts.js`.
- `scripts/create_examples.py`, `scripts/backtest.py`, `tests/`, `tests_web/test_api.py`.
- `docs/CATEGORY_MAPPING.md`, `docs/INTEGRATION.md`, `data/demo/consumer_001.csv`~`consumer_004.csv`, `source_materials/02_KeyFin_요구사항명세(2).md`.

명세의 FR-PAY-01·02·07·09는 정기 일정, 결제 준비, 수동 등록, 변동 공과금 예상액을 다룬다. FR-BGT-03은 승인 시점 봉투 차감과 청구 시 중복 차감 방지를 요구한다. 이 구분은 본 설계의 근거지만 원문이 아래 CSV 매핑이나 fixed_expense라는 엔진 종류를 직접 규정한 것은 아니다. 실제 이체와 계약 해지는 이번 범위 밖이다.

## 1. 현재 구조와 정정 사항

### 1-1. 종류 판정과 fallback

`normalize()`는 카드 대금 → ATM 출금 → 대출 상환 → 이체·저축 → 수입·정산 입금 순으로 특수 종류를 판정한다. 끝까지 expense인 거래만 `classify()`에 전달한다. 고정지출 판정도 이 특수 종류 판정 이후, 소비 봉투 매핑 이전에 적용해야 한다. 이름만 먼저 검사하면 입금·이체를 지출로 오분류할 수 있다.

현재 `classify()`는 정확한 세부분류가 없으면 `CATEGORY_ENVELOPE`로 원 상위 분류의 봉투를 선택하고 원 세부분류를 보존한다. 상위 분류도 모를 때만 기타로 간다. 예를 들어 `교통 > 새 교통수단`은 교통비다. 초안과 `CATEGORY_MAPPING.md` 끝부분의 “미등록 세부분류는 모두 기타 / 경조사·기타” 설명은 실제 코드와 다르다. 후속 구현에서 매핑 문서도 정정한다.

### 1-2. 종류와 보호 여부는 다르다

`flow()`는 현재 `is_fixed or is_recurring`이면 protected=True로 만든다. `_fit()`이 반복 규칙으로 잡은 흐름도 라벨과 무관하게 보호한다. 따라서 보호가 오직 반복 감지로 결정된다는 설명은 틀렸다. 반복 규칙이 없는 관리비도 라벨에 따라 보호되고 장보기 같은 소비도 규칙으로 보호될 수 있다.

고정지출 후보도 현재 expense이므로 consumption, by_envelope, total_expense, expected_expense에 포함된다. 다만 예산은 별개다. 관측 budget_amount_krw는 활성 expense이면서 exclude_tag=NONE일 때만 금액이 있고, 시뮬레이션은 budgeted를 검사한다. 모든 소비가 예산 초과 확률에 포함된다고 단정하지 않는다.

### 1-3. CSV 라벨 대조

| 원 세부분류 | 확인한 is_fixed / is_recurring / spend_pattern | 해석 |
|---|---|---|
| 월세, 통신, 인터넷 | TRUE / TRUE / FIXED | 분류는 명시적 대응표로 결정한다. |
| 전기요금, 가스요금, 관리비 | FALSE / TRUE / FIXED | 금액이 변해도 정기 지출일 수 있다. |
| 구독 | FALSE 또는 TRUE / TRUE / FIXED | 모든 구독의 is_fixed가 TRUE인 것은 아니다. |
| 자녀 학원 | FALSE / FALSE / PLANNED 또는 FIXED, TRUE / TRUE / FIXED | 동일 세부분류 안에서도 라벨이 섞인다. |
| 회비 | FALSE / FALSE / FIXED 또는 PLANNED | FIXED만으로 구독 계약으로 바꾸지 않는다. |
| 자동차세 | FALSE / FALSE / PLANNED, 1회 관측 | 한 건으로 연간 주기와 다음 납부일을 확정할 수 없다. |

## 2. 설계 원칙

1. 정확 매칭 표로 고정지출을 판정한다. 미등록 항목은 기존 소비 분류와 fallback을 유지한다.
2. 고정지출을 봉투에서 제외해도 자원·계좌·카드 흐름에서 누락하지 않는다.
3. fixed_expense는 일정 관리 대상이라는 뜻이다. 금액이 불변이거나 해지 불가능하다는 뜻은 아니다.
4. 원 분류·라벨·raw를 보존한다. 종류 분리와 주기 추정은 별개다.
5. 기존 Twin 재정규화는 활용하되 수동 일정·규칙 ID·예산까지 자동 이전된다고 보장하지 않는다.
6. 엔진은 금융망이 준 기록과 KeyFin이 확정한 분류만 입력으로 받는다. 반복·고정 여부 같은 추정 라벨은 입력이 아니라 엔진의 산출물이다. (2-1 절)

### 2-1. 입력 계약: 기록과 확정 분류

결정일 2026-09-08. 근거는 SSAFY 금융망 API 문서 대조 결과(`docs/FINANCE_API_CAPABILITIES` 아티팩트, 크롤링본 2026-08-24)와 KeyFin의 분류 확정 흐름이다. KeyFin은 가맹점명이 PG사이거나 분류가 불확실한 결제를 사용자에게 확정 요청하므로, 엔진에 도달하는 거래는 대부분 세부분류가 확정된 상태라고 전제한다.

#### 원칙

- 엔진 입력은 두 층뿐이다. **기록**(금융망 응답을 그대로 옮긴 값)과 **확정 분류**(KeyFin 매핑 표와 사용자 확정을 거친 값). 이 둘 외의 열은 받지 않는다.
- 기록은 검증만 하고 재해석하지 않는다. 확정 분류는 사실로 받되 확정 상태(`confirm_status`)에 따라 봉투 통계 포함 여부를 달리한다.
- 반복·고정 판정은 입력이 아니다. 엔진의 반복 규칙 감지, 고정지출 대응표(3-1), 금융망 스케줄 API를 옮긴 `snapshot.schedules` 세 곳에서만 나온다.

#### 열 분류

| 층 | 열 | 금융망 출처 | 처리 |
|---|---|---|---|
| 기록 | transaction_id, transaction_date, transaction_time, amount_krw | transactionUniqueNo, transactionDate, transactionTime, transactionBalance | 필수. 형식 검증만 |
| 기록 | account_id, card_id | accountNo, cardNo | 필수(둘 중 하나). 스냅샷 ID와 대사 |
| 기록 | transaction_type | 계좌: transactionTypeName 4종(입금/출금/입금(이체)/출금(이체)) → DEPOSIT/WITHDRAW/TRANSFER_IN/TRANSFER_OUT. 카드: 결제 내역 → CARD, 청구 인출 → CARD_BILL, 취소 → 원 거래 status CANCEL | 필수. 종류(kind) 판정의 1차 근거 |
| 기록 | merchant_id, merchant | merchantId, merchantName (카드만) | 카드 거래 필수, 계좌 거래 빈값 허용 |
| 기록 | status | 카드 취소 CANCEL → CANCELED, 그 외 NORMAL | 필수 |
| 기록 | user_id, source | userKey에 대응하는 앱 사용자 ID, 앱이 부여하는 SEED/LIVE | 필수 |
| 확정 분류 | category, subcategory | 금융망 아님. KeyFin 매핑 표(FR-TXN-10)와 사용자 확정의 산출물 | 필수. 3-1 고정지출 표와 봉투 표의 유일한 입력 |
| 확정 분류 | confirm_status | 금융망 아님. KeyFin 확정 흐름의 상태 AUTO / CONFIRMED / PENDING | 필수. 아래 PENDING 처리 |
| 사용자 행위 | exclude_tag | 금융망 아님. 더치페이·내부 이체·비상금 태그(FR-TXN-05) | 선택. 없으면 NONE. 내부 이체 판정과 예산 반영 여부에만 사용 |
| **제거** | direction, payment_method | transaction_type과 card_id 유무에서 도출 | 받지 않는다. 과도기에는 있어도 무시하고 일치 여부만 검증 |
| **제거** | is_fixed, is_recurring, spend_pattern, classify_source | 금융망 어느 응답에도 없음. 데모에서 세부분류마다 값이 모순(1-3 절) | 받지 않는다. 과도기에는 무시하고 `IGNORED_LABEL_COLUMNS` 경고 1회 |

`REQUIRED` 집합은 위 필수 열로 축소한다. 제거 열이 CSV에 있어도 거절하지 않고 읽지 않는다. 다음 매핑 버전에서 계약에서 삭제한다.

#### 라벨 제거가 엔진 동작에 미치는 변경

| 위치 | 현재 | 변경 후 |
|---|---|---|
| `flow()`의 protected | `is_fixed or is_recurring` 또는 규칙 감지 | 규칙 감지, fixed_expense 종류, 수동 일정만. 라벨 참조 삭제 |
| `_fit()`의 반복 힌트 | is_recurring 힌트가 있으면 관측 2회로 규칙 인정 | 힌트 삭제. 모든 그룹이 관측 3회 필요. 기간 중간에 시작한 월간 항목은 `snapshot.schedules`로 보완 |
| `_fit()`의 그룹 키 | 수입 그룹 키에 is_recurring 포함 | 제거. 수입은 가맹점·계좌만으로 묶음 |
| behavior.impulse_* | spend_pattern=IMPULSE 집계 | 삭제. 엔진이 이미 의미를 부인하던 값 |
| audit.warnings | PENDING_CLASSIFICATION 건수 경고 | 유지하되 아래 PENDING 처리와 연동 |

3-3 절의 "다른 소비의 기존 라벨 보호는 유지한다"와 "is_recurring 힌트가 없으면 최소 3개" 문장은 이 절이 대체한다.

#### PENDING 분류 처리

확정되지 않은 분류를 예측에 섞지 않되, 잔액 정확도는 분류와 무관하게 지킨다.

- **금액은 항상 포함**한다. 자원 변화, 계좌·카드 현금 흐름, 총소비(`consumption`)에 PENDING 행도 들어간다.
- **봉투 통계에서는 격리**한다. PENDING 행은 `envelope=None`, `budget_amount_krw=0`으로 정규화해 `by_envelope`, `budget_by_envelope`, 봉투 예산 초과 확률, 최적화 감축 대상에서 빠진다. 대신 `pending_consumption_krw`(관측)과 `pending_expense_p50_krw`(예측) 지표로 크기를 보인다.
- **비중이 크면 상태를 낮춘다**. 관측 소비 중 PENDING 금액 비율이 5%를 넘으면 결과 `status`를 `partial`로 두고 `PENDING_SHARE_HIGH` 경고에 비율과 건수를 싣는다. 봉투별 지표와 최적화 결과가 미확정 소비를 빼고 계산됐음을 명시한다.
- 고정지출 판정은 확정 세부분류에만 적용한다. PENDING 행은 세부분류가 고정지출 표에 있어도 `fixed_expense`로 승격하지 않는다.
- 확정되면 `apply_events`의 transaction 이벤트로 같은 ID의 갱신 행이 들어오고 재정규화로 제자리를 찾는다. 이때 `snapshot_dirty`는 세우지 않는다(잔액 변화가 없으므로). 현재 `TRANSACTION_CONFLICT` 검사는 서명 불일치를 거절하므로, confirm_status와 분류만 바뀐 갱신은 허용하는 예외 경로가 필요하다.

#### 금융망 필드와 이 설계의 접점

- 카드 청구 모델: 금융망은 월~일 결제를 다음 월요일 07:30 발행, 카드에 설정한 요일 16:00 인출한다. 스냅샷 `payment_delay_days` = 출금 요일 번호 − 1 (월요일 1 → 0, 수요일 3 → 2). 현재 시뮬레이션 식과 동일 구조다.
- 체크카드: 금융망에 체크카드가 없다(cardTypeCode 1만 존재). `DEBIT` 분기는 데모·수동 입력용으로 남기고 실데이터 경로에서는 나오지 않음을 문서화한다.
- 스케줄 원천: 예약이체(nextTransferDate, transferCycle), 정기결제(nextPaymentDate, billingCycle), 대출 일일 상환, 적금 일일 납입, 마이너스통장 nextInterestDate는 KeyFin이 `snapshot.schedules`로 변환해 넘긴다. 이 변환 어댑터는 엔진 밖이다. 3-4 절의 `FIXED_UNSCHEDULED` 대상은 스케줄 API에 없는 항목(자동차세 등)으로 좁아진다.
- 가맹점 확정 이력: 같은 merchant_id에 사용자가 확정한 세부분류가 있으면 `inspect().merchant_classifications`로 노출해 KeyFin이 다음 결제의 기본값으로 쓸 수 있게 한다. 엔진은 이 값을 스스로 적용하지 않는다.

#### 테스트 추가

1. 제거 열이 있는 CSV와 없는 CSV가 같은 Twin(`content_digest` 동일)을 만든다.
2. is_fixed=TRUE만 붙은 소비가 더 이상 보호되지 않고, 규칙 감지된 소비와 fixed_expense는 보호된다.
3. PENDING 행이 총소비·잔액에는 포함되고 봉투 합계·예산·최적화 후보 하한에서는 제외된다. 비율 5% 초과 시 `partial`.
4. PENDING → CONFIRMED 갱신 이벤트가 충돌 없이 반영되고 snapshot_dirty가 서지 않는다.
5. 데모 4명의 기존 결과와 비교해 잔액·자원 경로가 라벨 제거 전후로 같은 표본에서 동일하다(보호 변화로 감축 시나리오 결과만 달라질 수 있음을 별도 확인).

## 3. 데이터 모델 변경

### 3-1. 고정지출 그룹과 데모 전체 세부분류 대조

`FIXED_GROUPS`는 주거, 공과금, 통신, 보험·사회보험, 세금, 구독·멤버십의 6개다. 아래 원 세부분류를 `FIXED_LOOKUP`의 정확 매칭 키로 사용한다. 데모 번호는 파일 번호이며 user_id와 다르다.

| 원 세부분류 | 고정 그룹 | 등장 데모 | 처리 근거 |
|---|---|---|---|
| 월세 | 주거 | 001, 002 | 002의 동일 날짜 분할 납부는 기존처럼 합산한다. |
| 관리비 | 주거 | 003, 004 | 변동 금액 표본을 보존한다. |
| 전기요금 | 공과금 | 001, 002 | 변동 공과금이다. |
| 가스요금 | 공과금 | 001, 002, 004 | 변동 공과금이다. |
| 수도요금 | 공과금 | 없음 | 요청 범위에 따른 지원 항목이다. 데모 검증으로 주장하지 않는다. |
| 통신 | 통신 | 001~004 | 정기 통신료다. |
| 인터넷 | 통신 | 001, 003, 004 | 정기 인터넷 요금이다. |
| 실손보험 | 보험·사회보험 | 004 | 납부액만 분리하고 보장은 추정하지 않는다. |
| 사회보험 | 보험·사회보험 | 003 | 정기 납부액으로 분리한다. |
| 자동차세 | 세금 | 003 | 2026-06-26의 224,600원 1건이다. 주기는 미확정이다. |
| 구독 | 구독·멤버십 | 001, 002, 003 | 서비스별 반복 규칙을 이용한다. |
| 코워킹 | 구독·멤버십 | 003 | 반복 공간 이용료다. |

초안의 고정지출 목록에는 이 정책으로 분리할 데모의 11개 세부분류가 모두 있었다. 빠진 부분은 경계 항목의 처리 근거와 전체 대조였다. 아래 표까지 합치면 데모의 서로 다른 원 세부분류 78개를 모두 다룬다. 수도요금은 이 78개에 포함되지 않는다.

| 원 세부분류 | 변경 후 처리 | 경계·근거 |
|---|---|---|
| 헬스장, 수영 | expense / 의료·건강 / 운동·헬스 | 명세의 봉투 분류를 유지한다. 라벨·규칙 보호는 별도로 적용한다. |
| 도서, 온라인 강의, 스터디카페, 시험 응시료, 등록금, 자녀 캠프, 자녀 학원 | expense / 기타 / 교육 | 교육 봉투를 유지한다. 정기성만으로 옮기지 않는다. |
| 종교, 기부 | expense / 기타 / 경조사·기타 | 소비로 유지한다. 감축 보호는 라벨·규칙에 따른다. |
| 회비 | expense / 기타 / 경조사·기타 | 002·004에 있다. 모든 회비가 구독 계약이라는 근거는 없다. |
| 가족 용돈 | expense / 기타 / 경조사·기타 | 003·004의 지출이며 가족 지원 수입과 다르다. |
| 경조사, 세차 | expense / 기타 / 경조사·기타 | 기존 소비 분류를 유지한다. |
| 모임 정산 | 지출은 expense / 기타, 입금은 reimbursement | 001·002에서 방향이 다르다. 지출은 현재 category fallback이며 입금과 임의 상계하지 않는다. |
| 가족 생활비, 가족 지원, 급여, 알바 급여, 프로젝트 대금 | income | 데모의 실제 입금이다. 이름만 보고 고정지출로 바꾸지 않는다. |
| 연금 | income 또는 savings_out | 004의 연금 수입과 003의 저축·투자 이체를 구분한다. |
| 적금, 예금, 증권 | savings_out | 소비·고정지출에서 제외하고 자금 유출은 유지한다. |
| 대출 상환 | debt_service | 원리금 분해 없이 기존 종류를 유지한다. |
| ATM 출금 | cash_withdrawal | 이후 현금 소비는 미관측이다. |

나머지 데모 소비 세부분류는 기존 classify를 유지한다.

| 봉투 | 원 세부분류 |
|---|---|
| 외식 | 점심, 저녁/외식, 가족 외식, 고객 미팅, 빵·간식, 간식, 반찬, 모임, 카페, 배달 |
| 교통비 | 대중교통, 기차, 택시, 대리운전, 주차, 주유 |
| 의료·건강 | 병원, 약국, 한의원, 안경, 건강식품 |
| 취미·여가 | 영화/공연, 전시, 관람, 스포츠 관람, 게임, 노래방, 숙박, 놀이공원, 키즈카페, 문화센터 |
| 쇼핑 | 의류, 선물, 미용, 뷰티·건강, 장난감 |
| 편의점·마트·잡화 | 편의점, 장보기, 생활용품, 학용품 |

### 3-2. Transaction과 audit

`Transaction.fixed_group: str | None`을 추가한다. fixed_expense는 유효한 그룹, envelope=None, subcategory=None, budget_amount_krw=0, mapping_fallback=False로 둔다. 다른 종류의 fixed_group은 None이다. 취소 거래는 종류를 보존하지만 active=False이므로 모델·합계에서 빠진다.

현재 normalize는 위치 인수로 Transaction을 만들므로 필드 순서도 함께 맞춰야 한다. 구형 저장 행의 `Transaction(**stored)` 경로에는 누락 필드 처리가 필요하다. 기본값만 넣는 것은 구형 거래 재분류를 완료하는 것과 다르다.

audit의 kind_totals_krw에는 새 종류가 자연히 생긴다. 기존 envelope_totals_krw와 소비 전용 raw_category_totals_krw, raw_subcategory_totals_krw, mapping_quality는 expense만 대상으로 유지한다. 고정지출 조회를 위해 `fixed_totals_krw`(6개 그룹별 합계)와 `fixed_raw_subcategory_totals_krw`를 추가한다. 소비 합계=봉투 합계, 고정지출 합계=그룹 합계를 각각 대사한다.

### 3-3. flow와 반복 규칙

flow에 fixed_group을 추가하고 fixed_expense는 항상 protected=True, budgeted=False로 만든다. 다른 소비의 라벨 기반 보호(`is_fixed or is_recurring`)는 2-1 절에 따라 삭제하고, 보호는 규칙 감지·fixed_expense·수동 일정에서만 온다. 현재 component 키는 튜플 자체가 아니라 흐름 dict 전체의 `digest(f)`다. 추정 흐름·수동 일정·빈 모델의 기본 흐름에도 새 필드를 일관되게 넣는다.

반복 감지 알고리즘은 유지한다. 서로 다른 관측 날짜가 최소 3개여야 한다(2-1 절에 따라 is_recurring 힌트 경로는 삭제). 월간 또는 7·14·28일 간격 조건을 만족해야 한다. 같은 날 거래는 합산한다. 그룹 키에 kind가 들어가므로 고정지출 재분류 시 추정 rule_id가 바뀐다.

수동 일정은 flow를 호출하지 않고 별도 dict를 만들며 현재 budgeted=True다. 이 경로도 fixed_group과 fixed_expense의 budgeted=False를 반영해야 한다. 규칙 출력에는 kind와 fixed_group을 추가해 UI가 component 인덱스를 해석하지 않아도 되게 한다. 추정 규칙 교체 후 기존 evidence가 잔여 풀에 재진입하지 않는 동작은 유지한다.

### 3-4. 일정 미확정 고정지출

초안의 “1회 관측 고정지출은 일별 표에서 제외”는 채택하지 않는다. 미확정 비용을 0으로 만들면 자금 여력이 과대평가된다. 추천안은 규칙에 배정되지 않은 고정지출을 기존 residual 일별 표에 유지하고 `FIXED_UNSCHEDULED` 경고를 내보내는 것이다. 한 건뿐 아니라 반복 조건을 만족하지 못한 모든 고정지출이 대상이다.

경고에는 원 세부분류, 그룹, 거래 ID, 건수, 관측 합계, 마지막 날짜·금액을 담는다. 과거 빈도로 재표본 추출한 비용 가정이며 실제 납부일 예측은 아니라고 설명한다. audit.warnings와 엔진 결과에 전달한다. 자동차세를 연 1회로 단정하거나 미래 날짜를 자동 생성하지 않는다.

정확한 일정은 snapshot.schedules로 등록한다. 기존 추정 규칙에는 replaces_rule_id를 사용한다. 미확정 residual에는 교체할 rule_id가 없으므로 `schedules[].replaces_transaction_ids`라는 최소 계약 추가를 제안한다. 활성 fixed_expense 잔여 거래만 지정하고 그룹·계좌·카드가 같은지 검사한다. 알 수 없는 ID, 중복 교체, 이미 추정 규칙에 배정된 거래를 거부하며 replaces_rule_id와 동시 사용도 금지한다. 거래는 이력·감사에 남기고 미래 residual에서만 제외한다. 이 교체 계약을 구현하기 전에는 동일 비용을 residual과 수동 일정에 중복 등록하지 않도록 안내한다.

### 3-5. snapshot 검증

schedules.kind enum에 fixed_expense를 추가한다. 해당 종류는 fixed_group이 필수이고 envelope는 금지한다. 다른 종류에는 fixed_group을 금지한다. JSON Schema와 validate_snapshot을 함께 변경한다.

현재 validate_snapshot은 카드 일정이 expense가 아니면 INVALID_SCHEDULE_CARD를 낸다. expense와 fixed_expense만 허용하도록 변경한다. 계좌·카드 참조, 미래 날짜, 빈도별 필수 필드, known_bills 대사를 유지한다. replaces_transaction_ids는 비어 있지 않은 중복 없는 ID 배열로 제한하고 거래 존재·assigned 검증은 _fit에서 한다.

budgets는 기존 7개 소비 봉투만 받는다. 고정지출 그룹을 여덟 번째 봉투로 추가하지 않는다.

## 4. 시뮬레이션 변경

### 4-1. 집계와 카드 흐름

Simulation에 fixed(paths × days), fixed_by_group(paths × days × 6)을 추가한다. 위치 인수로 생성하는 Simulation 반환부도 맞춘다. consumption, by_envelope, budget_by_envelope는 기존 expense 분기를 유지하고 fixed_expense 집계를 추가한다.

현재 resource_delta는 income·reimbursement를 더하고 internal_transfer·card_settlement 외의 모든 종류를 뺀다. 새 fixed_expense도 이미 차감 대상이다. 별도 차감을 추가하면 이중 차감된다. 계좌 흐름도 입금·내부 이체 이외에는 차감하므로 기존 경로를 사용한다.

카드는 수정이 필요하다. `if not ready: continue` 뒤의 `if kind!='expense': raise UNSUPPORTED_CARD_FLOW`를 두 지출 종류 허용으로 넓힌다. 잔액·카드 정책이 없는 history-only 실행은 이 검사에 도달하지 않으므로 카드 지원 검증을 대신할 수 없다.

- 체크카드는 구매·납부일에 현금과 자원을 줄인다.
- 신용카드는 구매일에 자원을 줄이고 payable을 늘린다. 다음 월요일 + payment_delay_days에 현금과 payable을 함께 줄인다.
- known_bills는 기초 미결제액 정산이다. consumption·fixed·resource에서 다시 차감하지 않는다.

### 4-2. 캘린더

현재 반복·수동 이벤트 이름은 `'RECURRING_' + kind.upper()`다. 종류만 추가하면 RECURRING_FIXED_EXPENSE가 된다. 초안의 FIXED_EXPENSE가 자동 생성되는 것은 아니다. 추천안은 기존 생성 규칙을 유지해 `RECURRING_FIXED_EXPENSE`를 공식 계약으로 삼는 것이다. ONCE 수동 일정도 현재 같은 접두사를 쓰므로 빈도는 규칙 정보로 확인한다.

캘린더 행에 fixed_group을 추가하되 해당하지 않으면 null로 둔다. CARD_BILL에는 소비와 고정지출이 섞일 수 있으므로 그룹은 null이다. 고정지출 구매 이벤트와 카드 청구 이벤트를 합쳐 총지출로 계산하지 않는다. timing_basis, horizon 밖 청구의 within_forecast_horizon과 amount_coverage는 유지한다.

residual은 현재 캘린더 행을 생성하지 않는다. 미확정 고정지출은 합계·경고로 표시하며 임의의 표본 날짜를 확정 납부일로 보여 주지 않는다. 명시적 고정지출 cash_event는 SCENARIO_FIXED_EXPENSE로 구분한다.

### 4-3. 시나리오 배율과 금액 교체

| 필드 | 적용 범위 |
|---|---|
| expense_reductions | expense의 비보호 residual만 감축한다. 반복·수동 일정은 기존처럼 제외한다. |
| expense_multiplier | expense 흐름 전체에 적용한다. 보호 소비는 포함하고 fixed_expense는 제외한다. |
| fixed_multiplier | 신규, 기본 1, 0~5. 고정지출 residual과 규칙에 적용한다. |
| fixed_overrides | 신규 [{rule_id, amount_krw}]. 현재 fixed_expense 규칙의 각 발생 금액을 교체한다. |
| cancel_rule_ids | 알려진 전체 규칙에 대한 기존 취소 기능을 유지한다. 실제 계약 해지가 아니다. |
| cash_events[].fixed_group | EXPENSE에만 허용한다. 있으면 fixed·그룹에, 없으면 기존 기타 소비·예산에 집계한다. INCOME에 주면 거부한다. |

순서는 ID 검증 → 취소 → 규칙별 금액 교체 → 종류별 배율 → 집계다. override는 배율 적용 전 기준 금액이며 각 미래 발생에 동일 적용한다. 중복 override, 미등록 ID, 비고정 규칙, 취소와 교체의 동일 ID 동시 지정은 오류다. 특정 월만 교체하는 기능은 이번 범위에서 제외한다.

현재 캘린더는 mean(values) × factor를 반올림하지만 실제 흐름은 각 경로를 _scaled로 반올림한다. 변경 시 해당 발생의 변환된 표본 평균으로 expected_amount_krw를 계산해 반올림 차이와 배율 이중 적용을 막는다.

cash_events는 현재 흐름 배율 처리 후 추가하는 확정 가정 금액이다. 고정지출 이벤트도 fixed_multiplier를 추가 적용하지 않는다. 소비 이벤트가 expense_multiplier의 영향을 받지 않는 현재 의미와 일치시킨다. 날짜·계좌 검증을 유지하고 override 적용 후 금액까지 MONEY_RANGE_LIMIT로 검사한다.

### 4-4. 금융 불변식

경로별·일별로 다음을 검사한다. 각 항은 취소·교체·배율 적용 후 금액이다.

```text
resource_delta = income + reimbursement + 현금 이벤트 입금
                 - consumption - fixed - debt_service - cash_withdrawal - savings_out
```

consumption·fixed에는 해당 현금 이벤트 지출이 이미 포함되므로 다시 빼지 않는다. 내부 이체와 카드 대금 정산은 자원 변화에서 제외한다. fixed == fixed_by_group.sum(axis=2)를 검사하고, snapshot 준비 시 기존 free - free[:, [0]] == resource 및 payable 비음수 불변식을 유지한다.

분류만 바꾸고 동일 금융 표본을 사용할 때 현금·자원을 보존한다. 버전 간 같은 seed만으로 경로·평균이 같다고 보장하지 않는다. 종류가 바뀌면 규칙 정렬과 난수 사용 순서가 달라질 수 있다. 통제된 표본의 금액 보존과 데모 통계 재산출을 구분한다.

## 5. 엔진 출력

### 5-1. 공통 지표

H는 예측일수, C와 F는 경로별 기간 소비·고정지출 합계다.

| 지표 | 정의 |
|---|---|
| total_expense_p10/p50/p90_krw, expected_expense_krw | C의 분위수·평균. 기존 키를 유지하고 basis=simulation_consumption_only로 구분한다. |
| total_fixed_p10/p50/p90_krw, expected_fixed_krw | F의 분위수·평균. 고정지출 기준임을 basis에 명시한다. |
| fixed_monthly_p50_krw | P50(F) × 30.4375 / H. 예측 기간의 월 환산값이며 확정 월 청구액이 아니다. |
| fixed_share_of_outflow | mean(F) / mean(C+F), unit=ratio. 분모 0이면 null. |
| total_outflow_p50_krw | P50(C+F). 화면에는 “소비+고정지출”로 표시한다. 저축·대출·ATM까지 포함한 전체 현금 유출이 아니다. |

P50(C)+P50(F)는 P50(C+F)의 대체값이 아니다. 경로별로 합산한 뒤 분위수를 구한다. 기대값 합계는 반올림 전 검산하거나 원 단위 반올림 오차를 허용한다.

fixed_groups 데이터셋은 [{group, p10_krw, p50_krw, p90_krw}]로 6개 그룹을 항상 내보낸다. envelopes는 7개를 유지한다. daily_rows의 cumulative_expense_p50_krw도 소비 전용으로 바뀌므로 cumulative_fixed_p50_krw를 추가한다. 새 고정지출 bar와 “봉투별 예상 소비 (고정지출 제외)” 제목을 사용한다.

### 5-2. 모드별 영향

| 모드 | 변경 |
|---|---|
| forecast | 공통 지표와 데이터셋을 추가한다. |
| what_if | 공통 지표는 현재처럼 무가정 baseline이다. paired_expense_saving은 baseline 소비 - branch 소비다. paired_fixed_delta_p50_krw는 branch F - baseline F로 정의해 양수가 비용 증가임을 표시한다. branch 고정 그룹·누적 데이터도 제공한다. |
| goal | free - reserve >= target 계산은 유지한다. decision에 월 고정지출 참고값을 추가하며 잔액 미확정 early return에서도 계산 가능한 참고값은 유지한다. |
| risk | 고정지출 10% 인상 기본 가정을 추가한다. fixed_coverage_months와 소비 전용 budget_risk를 제공한다. |
| optimize | 소비 봉투 유한 감축 후보를 유지한다. 고정지출 취소·교체는 자동 탐색하지 않는다. 요청의 고정지출 시나리오는 모든 후보에 동일 적용하고 decision에 월 환산 참고값을 넣는다. |

fixed_coverage_months는 max(기초 free, 0) / fixed_monthly_p50_krw, unit=months로 제안한다. 잔액 미확정 또는 분모 0이면 null이다. 예비자금을 빼는 지표가 아니며 소득·소비·실제 결제일을 반영한 생존 개월 수도 아니다.

#### _budget_risk의 관측·미래 집계

관측 사용액은 활성 거래 중 봉투와 기준월이 같은 budget_amount_krw 합계다. 현재 함수 자체에는 kind==expense 조건이 없다. fixed_expense의 envelope=None, budget_amount_krw=0 정규화가 전제돼야 제외된다. 종류 조건도 명시하는 것을 추천한다.

미래 사용액은 sim.budget_by_envelope의 기준월 말까지 합계다. 관측과 미래 양쪽에서 고정지출이 빠져야 한다. 기존 관측 시작일·예측 기간 부족 경고와 full_month_forecast_coverage는 유지한다.

#### _optimize의 잔여 소비 하한

현재 minimum_remaining_monthly_krw는 다음 조건과 식으로 계산한다.

```text
대상: kind == expense AND envelope == 대상 봉투 AND protected == False
remaining = Σ mean(bundle.variable의 기간 합계)
            × (1 - 최종 감축률) × expense_multiplier × 30.4375 / H
```

보호 residual, 반복·수동 일정, cash_events는 포함되지 않는다. fixed_expense도 포함되지 않는다. 이 의미는 INTEGRATION.md의 “잔여 변동 소비”와 UI의 “봉투별 최소 월 변동소비”에 맞으므로 유지한다. sim.by_envelope 전체 평균으로 바꾸면 보호 소비로 하한을 충족하는 의미 변경이 생긴다. 현재 식은 경로별 원 단위 반올림과 미세하게 다를 수 있는 기대값 근사다.

### 5-3. inspect

behavior.fixed_monthly_observed_krw는 활성 고정지출 합계 × 30.4375 / 실제 관측일수다. fixed_groups_observed_krw는 그룹별 관측 기간 합계로 정의한다. 예측 월 환산과 구분한다. 소비 요일 평균·충동 라벨 통계는 expense 필터를 유지한다.

recurring_rules의 종류·그룹과 audit의 고정지출 합계를 노출한다. relationships는 현재 envelope가 없는 거래에 분류 관계를 만들지 않는다. 고정지출 분류를 표시하려면 fixed_group 노드·관계를 추가하고 노드 ID에 fixed-group: 접두사를 사용한다.

## 6. 스키마·API·UI·운영 영향

### 6-1. 스키마와 병합

request.json은 scenario와 stress_scenarios[].items의 필드 정의를 중복하고 additionalProperties=false다. 양쪽에 fixed_multiplier, fixed_overrides, cash_events의 fixed_group과 조건부 검증을 추가한다. override는 최대 100개, rule_id는 1~200자, 금액은 정수 0~10^12로 기존 한도와 맞춘다. 규칙 종류·중복·충돌은 런타임에서 확인한다.

_merge_scenario는 expense_reductions만 키별 병합하고 나머지는 통째로 덮어쓴다. fixed_overrides·cancel_rule_ids도 배열 전체 교체로 유지한다. 병합 결과의 취소·교체 충돌도 검사한다. stress에서 fixed_multiplier를 생략하면 기준값을 유지하고 1을 명시하면 기준값을 1로 덮어쓴다.

result.json은 지표 키를 열어 두지만 unit은 enum이다. fixed_coverage_months의 months를 추가하고 UI 단위 표시도 맞춘다. 구조 schema_version=1.0은 유지하는 안을 제안하되 의미 변경은 모델·매핑 버전과 basis에 명시한다. 구조 버전만 확인하는 소비자는 새 의미를 구분하지 못하므로 통합 문서에 버전 확인을 요구한다.

### 6-2. API와 UI

GET /api/config에 fixed_groups를 추가하고 /api/health의 engine_version도 실제 엔진 버전에 맞춘다. config는 예제 템플릿·request/snapshot 스키마를 반환하므로 해당 파일의 변경이 화면에 전파된다. 새 엔드포인트는 필요하지 않다. /api/validate/request는 스키마 검증만 수행하므로 rule_id 존재까지 보장하지 않는다.

app.js의 변경 지점은 다음과 같다.

- scenarioFields: 고정지출 배율·규칙 금액 교체·현금 이벤트 그룹을 추가한다. 기존 전체 일정 취소 기능을 유지하고 실제 계약 해지로 표시하지 않는다.
- stressFields와 add-stress: 새 배율과 생략/명시적 1의 차이를 지원한다. JSON↔폼 전환에서 새 필드를 보존한다.
- METRIC_NAMES, renderOutput의 모드별 카드 목록, metricValue: 고정지출 카드·개월 단위·비교 부호를 추가한다. 전체 Metrics 표에 자동 노출돼도 주요 카드에 자동 추가되지는 않는다.
- 현재 Twin inspect는 twin_state.json 다운로드다. 이미 상세 화면이 있다고 가정하지 말고 활성 Twin 요약 영역에 관측 고정지출·그룹·경고를 추가한다.
- 경고 탭은 현재 code·message만 표시한다. 미확정 거래의 날짜·금액 등 구조화된 상세도 확인하게 한다.

차트는 visualizations를 순회하므로 새 bar 명세를 이용한다. charts.js의 표는 키를 동적으로 표시해 새 event_type·fixed_group을 렌더링할 수 있다. 이것이 외부 이벤트 필터의 호환성까지 보장하지는 않는다.

현재 수동 snapshot 폼은 계좌·카드 중심이다. 고정지출 일정·교체 ID는 우선 기존 snapshot JSON 업로드 경로로 제공하고 입력 예시를 안내한다. 미구현 일정 편집 UI가 있다고 가정하지 않는다.

### 6-3. store.py의 apply_events

새 이벤트 type은 필요 없다. transaction·cancel_transaction은 이미 normalize를 호출하고 마지막에 Twin을 재생성한다. reducer 본문이 크게 바뀌지 않더라도 아래를 영향 범위로 검증한다.

1. LIVE 고정지출 추가·취소는 snapshot_dirty를 설정한다. 권위 잔액을 엔진이 직접 차감·복원하지 않는다.
2. 취소는 원 raw의 status를 바꿔 재정규화하며 고정지출 감사 합계와 규칙 evidence에서 빠져야 한다.
3. snapshot 이벤트는 새 일정 검증을 통과해야 한다. 취소·수정으로 일정 교체 대상이 무효가 되면 배치를 명시적으로 거부하고 수정된 snapshot과 함께 재시도하도록 안내한다.
4. 원자성, event_id 멱등성, 사용자 검증, revision/CAS를 유지한다. event_log의 원 이벤트 digest를 분류 변경 때문에 재작성하지 않는다.

### 6-4. CLI와 예제 생성

cli.py의 build·inspect·run --request·update --events는 같은 Twin·Engine·Store를 사용한다. 새 전용 플래그를 만들지 않고 JSON으로 전달한다. 새 출력, 저장 후 재로딩, 규칙 오류의 exit code 2를 검증한다.

create_examples.py의 예산은 audit.envelope_totals_krw × 30.4375 / observation_days × 1.1을 천 원 단위로 반올림한다. 분류 변경 후 같은 식으로 다시 만든다. 4개 파일의 관측일수는 모두 90일이 아니다.

| 데모 | 관측일수 | 기존 기타 | 분리 고정지출 | 남는 기타 소비 | 같은 생성식의 새 기타 예산 |
|---|---:|---:|---:|---:|---:|
| 001 | 90 | 2,930,200 | 2,643,700 | 286,500 | 107,000 |
| 002 | 88 | 4,320,470 | 1,910,470 | 2,410,000 | 917,000 |
| 003 | 90 | 5,273,510 | 3,875,110 | 1,398,400 | 520,000 |
| 004 | 89 | 2,068,300 | 1,088,300 | 980,000 | 369,000 |

단위는 원이다. 현재 코드로 정규화한 활성 expense에서 3-1의 분리 대상을 합산한 관측 검산이며 새 엔진의 실행 결과가 아니다. 001 고정지출은 월세 2,100,000 + 전기 167,600 + 통신 165,000 + 인터넷 99,000 + 구독 73,200 + 가스 38,900원이다.

스크립트는 snapshot 4개뿐 아니라 examples/requests/*.json도 덮어쓴다. risk 예제가 stress 배열을 명시하므로 엔진 기본 충격만 바꿔서는 새 고정지출 충격이 보이지 않는다. 예제 risk 요청에도 반영한다. 데모 잔액의 USER_ASSUMPTION 표시는 유지하고 CSV는 수정하지 않는다.

### 6-5. backtest와 관련 문서

backtest.py의 actual_values는 active expense만 소비로 합산한다. 새 분류로 관측 소비 정답과 최근 28일 단순 비교값이 함께 줄어든다. resource는 내부 이체·카드 정산을 제외한 지출을 차감하므로 fixed_expense도 이미 포함되는 구조다.

고정지출 관측·예측·오차·구간 포함 여부와 소비+고정지출 비교값을 추가한다. 원 단위 반올림 기준을 맞추고 학습 cutoff 뒤의 정보를 일정 추정에 사용하지 않는다. 기존 8개 합성 검증 창의 소비 MAE와 새 소비 MAE를 같은 정의로 비교하지 않는다. 모델·매핑 버전을 기록하고 artifacts/backtest.json을 다시 만든다. 합성 이력 검증은 실사용 확률 보정이나 실제 잔액 백테스트가 아니다.

CATEGORY_MAPPING.md는 종류·고정 그룹·방향별 예외를, INTEGRATION.md는 배율·일정·출력·하한 의미를 갱신한다. README와 QA_REPORT는 후속 구현 검증 후 갱신한다. 이번에는 이 파일들을 수정하지 않는다.

## 7. 마이그레이션과 호환성

| 항목 | 영향·추천 처리 |
|---|---|
| 원 거래 재정규화 | from_dict는 raw가 있고 normalize가 성공하면 새 분류를 적용한다. 실패하거나 raw가 없으면 Transaction(**stored) 경로다. 새 필드 누락을 처리하고 미이전 상태와 원 CSV 재생성 경로를 제공한다. 잘못된 raw를 성공적으로 이전했다고 간주하지 않는다. |
| 추정 규칙 ID | kind가 해시 키에 포함되어 고정지출 rule_id가 바뀐다. 기존 cancel_rule_ids·replaces_rule_id가 무효가 될 수 있고 후자는 Twin 로딩도 실패시킬 수 있다. 원 evidence·가맹점·채널로 유일하게 대응될 때만 명시적으로 이전하고 모호하면 재지정을 요구한다. |
| 구형 수동 expense 일정 | raw 재정규화 대상이 아니다. 월세 일정이 expense/기타로 남을 수 있다. 용도를 확인해 새 종류·그룹으로 이전하며 추정 규칙과 중복되지 않게 한다. 모든 기타 일정을 이름·금액만으로 옮기지 않는다. |
| 예산 | 기존 budgets.기타는 자동 축소하지 않는다. BUDGET_MAY_INCLUDE_FIXED는 분리 대상 이력이 있고 예산이 관측 잔여 소비 월 환산의 3배를 넘을 때 참고 경고로 제안한다. 소비 0·양수 예산도 처리한다. 짧은 이력·일회성 교육비 때문에 휴리스틱이지 오류 판정은 아니다. |
| digest·revision | signature와 모델·매핑 버전이 content_digest를 바꾼다. 로딩만으로 revision·event_log를 바꾸지 않는다. 결과 비교·캐시는 input_digest와 버전도 확인한다. |
| 배율·지표 | expense_multiplier와 소비 지표의 범위가 줄어든다. 두 종류를 함께 올리려면 두 배율을 명시한다. 이전 결과는 당시 버전·basis와 보관한다. |
| 캘린더 | RECURRING_EXPENSE에서 RECURRING_FIXED_EXPENSE로 이동한다. 외부 필터를 갱신한다. CARD_BILL은 유지한다. |
| 재현성 | 같은 버전·입력·seed의 재현성은 유지한다. 버전 간 금액 보존과 난수 경로 일치는 별도 검증한다. |

## 8. 테스트 계획

이번 작업은 설계 문서만 수정한다. 아래는 후속 구현의 검증 계획이다. 기존 fixture를 무조건 다른 소비로 바꿔 실패를 숨기지 않는다.

| 실제 기존 테스트·위치 | 유지·수정·추가할 단정 |
|---|---|
| tests/test_ingest.py::test_actual_inputs | 행 수 337/193/262/188, 사용자, SEED, 대사 0은 유지한다. 78개 세부분류 대조와 그룹 합계를 추가한다. |
| test_unknown_mapping_uses_raw_category_before_other, test_unknown_category_preserves_raw_subcategory | 기존 fallback 단정을 유지한다. 미지 세부분류의 일괄 기타 변경을 막는다. |
| test_budget_exclusion_does_not_erase_cash, test_mapping_audit_exposes_raw_categories_and_quality | 점심 fixture 단정을 유지한다. 고정지출의 원 라벨 보존·budget=0·취소·그룹 대사를 추가한다. 수도요금은 합성 행으로 검사한다. |
| tests/test_model.py::test_same_day_rent_aggregated | 002 월세 amount_samples=[550000]*3은 유지하고 새 종류·그룹을 확인한다. |
| test_rules_not_duplicated_in_pool | 미확정 residual 유지안이므로 기존 합계 불변식을 유지한다. 자동차세가 남고 경고가 발생하는지 검사한다. 수동 거래 교체 후에는 지정 비용만 미래 풀에서 빠져야 한다. |
| test_manual_schedule_override_does_not_add_twice | 현재 월세 fixture는 expense/기타/600000이다. fixed_expense/주거로 변경하고 추정 규칙 제거·중복 방지·소비 제외를 검사한다. |
| test_model_round_trip, test_relationship_graph_references_exist | fixed_group 직렬화, 구형 raw 유무·정규화 실패, 이전 규칙 참조, 그룹 노드 참조 무결성을 추가한다. |
| tests/conftest.py::make_exact | 현재 expense만 카드 채널을 만든다. fixed_expense의 CREDIT/DEBIT와 그룹을 명시적으로 만들 수 있게 확장한다. |
| tests/test_simulation.py의 체크·신용·known_bill 검증 | 계좌·체크·신용 고정지출, horizon 밖 청구, history-only를 추가한다. 구매·청구 시 소비·고정지출 이중 집계가 없어야 한다. |
| test_fixed_protected_variable_reduced, tests/test_modes.py::test_fixed_spending_never_reduced_by_optimizer | 기존 fixed는 expense에 붙은 보호 표시다. 그 검증을 유지하고 새 종류의 독립 보호·배율 검증을 추가한다. |
| test_cancel_recurring_keeps_bundle_and_twin_intact | 월세 취소 시 자원 700000원 증가와 Twin 불변성을 유지한다. fixed 감소, consumption 불변, 새 이벤트 이름도 검사한다. |
| test_budget_observation_and_horizon_partial | 점심 관측 사용액 100원 단정은 유지한다. 같은 월 고정지출이 관측 사용액·미래 budget 양쪽에서 제외되는지 추가한다. |
| optimize 하한 | 비보호 residual·보호 소비·고정지출·규칙·cash_event를 섞어 하한이 비보호 residual만 보는지 검사한다. 경계값·배율도 확인한다. |
| tests/test_modes.py | 4명×5모드, null, JSON 유한수, ChartSpec 검증을 유지한다. 공통 고정지표·6그룹·7봉투, what_if 부호, 0 분모를 추가한다. |
| tests/test_store.py | LIVE 고정지출 추가·취소·새 snapshot과 멱등성·원자성·stale snapshot·CAS를 기존 소비 사례와 함께 검사한다. |
| tests/test_cli.py::test_cli_build_inspect_and_run | 새 inspect 집계, run --request의 고정지출 시나리오, update 및 오류 exit code 2를 추가한다. list-modes 5개는 유지한다. |
| tests_web/test_api.py::test_config_uses_engine_contract | 템플릿 5개·봉투 7개는 유지하고 그룹 6개를 추가한다. 스키마 검증과 4명×5모드 직접 엔진 결과 일치를 유지한다. |
| tests/test_renderer.py 및 UI 검증 | escape·null 단정을 유지한다. 새 bar/table, 개월 단위, 주요 카드, JSON↔폼 왕복, 경고 상세를 검사한다. |

새 시나리오 검증에는 override 금액 0·상한, 중복·미등록·비고정 ID·취소 충돌, stress 병합 후 충돌, 배율 0·1·5, income 이벤트의 그룹 금지, 변환 후 금액 범위와 캘린더 평균 일치를 포함한다.

데모 회귀는 6-4의 관측 합계와 예산을 검산한다. “새 expected_expense + expected_fixed가 이전 expected_expense와 같은 seed에서 정확히 일치” 단정은 제거한다. 통제된 동일 표본으로 경로별 소비+고정지출·현금·자원 보존을 검사하고 데모 통계는 새 버전으로 재산출한다.

## 9. 후속 구현 순서

1. 분류표와 추천안을 계약으로 확정하고 mapping·normalize·audit 및 분류 검증을 변경한다.
2. model의 흐름·수동 일정·미확정 경고·거래 교체·snapshot·구형 로딩을 구현한다. store 이벤트와 CLI 재로딩을 검증한다.
3. simulation의 카드 허용·집계·배율·교체·캘린더 및 금융 불변식을 구현한다.
4. engine 지표·예산 위험·하한 계약과 request/result 스키마를 검증한다.
5. 예제 생성기의 요청·snapshot 덮어쓰기 범위를 확인해 4개 데모와 백테스트를 갱신한다.
6. Workbench 폼·카드·경고와 API를 검증하고 관련 문서·보고서를 갱신한다.

현재 작업에서 수정하는 파일은 이 설계 문서 하나다. 위 내용은 구현 완료나 테스트 통과 보고가 아니다.

## 10. 열린 질문별 추천안과 근거

| 질문 | 추천안 | 근거·재검토 조건 |
|---|---|---|
| 구독을 고정지출로 볼 것인가? | fixed_expense/구독·멤버십으로 분리한다. 취소는 cancel_rule_ids, 요금 변경은 fixed_overrides로 가정한다. | FR-PAY-01이 구독을 정기 일정에 포함한다. FR-BGT-10의 미사용 구독 절감 안내는 별도로 유지하되 소비 봉투에 포함해야 한다는 뜻은 아니다. 실제 해지는 실행하지 않는다. |
| 헬스장·수영 회원권도 분리할 것인가? | 이번 버전에서는 의료·건강/운동·헬스 소비를 유지한다. | 명세가 봉투 안에 명시하고 CSV에는 정기·일회 이용이 섞인다. 계약별 신뢰할 수 있는 납부 정보가 생기면 운영 매핑을 재검토한다. 라벨·규칙 때문에 감축은 보호될 수 있다. |
| 자녀 학원 등을 사용자가 세부분류별로 고정 재지정할 수 있게 할 것인가? | snapshot.fixed_overrides_by_subcategory 같은 사용자 전역 매핑은 제외한다. | FR-TXN-10은 매핑을 시스템·운영 데이터로 두고 사용자 편집 없음이라고 명시한다. FR-PAY-07의 개별 일정 등록과 전역 분류 편집은 다르다. 학원은 교육 소비를 유지하며 수동 일정 등록이 기존 소비를 자동 제거하지 않는다. |
| consumption_multiplier를 신설하고 expense_multiplier를 두 종류의 별칭으로 남길 것인가? | 기존 필드를 소비 전용으로 좁히고 fixed_multiplier만 추가한다. | 세 필드의 우선순위·충돌을 늘리지 않는다. 현재 로컬 템플릿을 함께 이전한다. 이전 의미가 필요한 요청에는 두 배율을 명시한다. 외부 무중단 호환이 필수로 확인되면 별도 API 버전을 검토한다. |
| 일정 미확정 고정지출을 제거할 것인가? | residual을 유지하고 FIXED_UNSCHEDULED를 표시한다. | 한 건으로 미래 주기를 확정할 수 없고 완전 제거는 비용 누락이다. 정확한 일정은 명시적 거래 ID 교체로 중복을 막는다. |
| 잔여 소비 하한을 봉투 전체로 넓힐 것인가? | 현재 비보호 residual 하한을 유지한다. | _optimize·통합 문서·화면이 같은 의미를 사용한다. 보호 비용으로 하한을 충족해 감축 가능한 생활 소비가 과도하게 줄어드는 의미 변경을 피한다. 전체 소비 하한은 별도 계약으로 검토한다. |

## 11. 검토자 메모

- 보호가 규칙 감지뿐 아니라 CSV 라벨에서 시작한다는 점과 상위 카테고리 fallback을 정정했다.
- 특수 종류 판정 뒤에 고정지출 매핑을 적용하도록 명시해 수입·저축의 오분류를 막았다.
- 데모 원 세부분류 78개를 대조하고 회비·가족 용돈·연금 등 경계 항목, 데모에 없는 수도요금을 명시했다.
- 실제 관측일수 90/88/90/89일과 고정지출·기타 합계, 생성식에 따른 예산을 추가했다.
- resource_delta의 포괄 차감과 카드 검사 실행 조건을 설명해 이중 차감·history-only 검증 누락을 막았다.
- 캘린더의 실제 동적 이름과 구매·청구 중복 집계, 배율·반올림 차이를 보완했다.
- _budget_risk의 관측·미래 집계와 _optimize의 비보호 residual 하한 수식을 명시했다.
- 미확정 비용 삭제안을 residual 유지·경고로 바꾸고 수동 일정 중복 방지를 위한 거래 ID 교체 계약을 제안했다.
- override 적용 순서·충돌, stress 배열 병합, cash_event 배율 제외·그룹 검증을 구체화했다.
- raw 재정규화의 한계, 규칙 ID 변경, 구형 수동 일정, digest와 revision의 차이를 추가했다.
- store.apply_events, CLI, 예제 생성기, backtest, 실제 기존 테스트 단정과 UI·결과 스키마를 영향 범위에 포함했다.
- 기존 열린 질문 4개에 추천안과 근거를 붙이고 미확정 비용·하한의 추가 결정을 남겼다.
- 버전 간 같은 seed의 통계 일치를 보장하지 않도록 검증 계획을 정정했다. 코드·CSV·예제·테스트는 수정하지 않았다.

## 12. 변경 이력

- 2026-09-08 초안 작성(Claude), 같은 날 gpt-6-astra 검토로 실제 코드 분기·영향 범위·데모 대조 보완.
- 2026-09-08 2-1 절 "입력 계약" 추가. 금융망 API 대조 결과와 KeyFin 분류 확정 흐름을 근거로 추정 라벨 열 제거, PENDING 격리 방침, 스케줄 API 접점을 정의. 3-3 절의 라벨 보호·힌트 문장을 이에 맞게 정정.
- 2026-09-08 구현 완료(`IMPL_CONTRACT_FIXED_EXPENSE.md` 기준). 구현 중 설계와 달라진 결정 세 가지:
  1. 종류 판정: `TRANSFER_OUT`은 내부 이체의 근거가 아니다. 축의금·회비·모임 정산 송금이 `TRANSFER_OUT`이므로 소비로 남기고, 내부 이체는 `exclude_tag`(INTERNAL_TRANSFER/SELF_TRANSFER), `direction=TRANSFER`, 저축·투자 카테고리로만 판정한다.
  2. 반복 규칙 감지는 관측 날짜 전체가 주기를 만족할 때만 인정한다. 날짜 부분집합 탐색은 자주 가는 가맹점을 가짜 주간 규칙으로 만들어 채택하지 않았다. 단, 같은 가맹점의 일회성 송금(금액이 중앙값의 ±50% 밖)은 한 번 제외하고 재시도한다(consumer_003의 프로젝트 대금 사례).
  3. 미확정 고정지출은 3-4 절대로 일별 표에 유지하고 `FIXED_UNSCHEDULED`로 보고한다. 데모에서는 consumer_003 자동차세 1건, consumer_004 실손보험 2건이 해당한다.
  결과: 전체 테스트 260개 통과. 데모 4개 소비 건수는 `expense`+`fixed_expense`로만 나뉘고 다른 종류는 불변(001: 301+21=322, 002: 158+21=179, 003: 212+34=246, 004: 152+14=166). 데모 스냅샷 재생성으로 001의 `기타` 예산이 1,090,000원에서 107,000원으로 내려갔다.
