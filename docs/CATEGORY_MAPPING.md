# CSV → 엔진 분류 어댑터

첨부 요구사항의 7봉투·22세분류를 사용합니다. 아래는 실제 CSV의 원 세분류를 해석한 **구현 매핑**이며 원문에 직접 정의된 정답표가 아닙니다. 원 category/subcategory는 모든 거래에 보존합니다. 소득·정산·저축·ATM·원리금·카드 대금은 소비 봉투에 넣지 않습니다.

|CSV 원 세분류|종류(kind)|고정 그룹|예산 봉투|KeyFin 세분류|
|---|---|---|---|---|
|ATM 출금|cash_withdrawal|—|—|—|
|가스요금|fixed_expense|공과금|—|—|
|가족 생활비|income|—|—|—|
|가족 외식|expense|—|외식|음식점|
|가족 용돈|expense|—|기타|경조사·기타|
|가족 지원|income|—|—|—|
|간식|expense|—|외식|음식점|
|건강식품|expense|—|의료·건강|병원·약국|
|게임|expense|—|취미·여가|게임·콘텐츠|
|경조사|expense|—|기타|경조사·기타|
|고객 미팅|expense|—|외식|음식점|
|관람|expense|—|취미·여가|영화·공연·전시|
|관리비|fixed_expense|주거|—|—|
|구독|fixed_expense|구독·멤버십|—|—|
|급여|income|—|—|—|
|기부|expense|—|기타|경조사·기타|
|기차|expense|—|교통비|대중교통|
|노래방|expense|—|취미·여가|게임·콘텐츠|
|놀이공원|expense|—|취미·여가|여행·숙박|
|대리운전|expense|—|교통비|택시|
|대중교통|expense|—|교통비|대중교통|
|대출 상환|debt_service|—|—|—|
|도서|expense|—|기타|교육|
|등록금|expense|—|기타|교육|
|모임|expense|—|외식|음식점|
|모임 정산|expense|—|기타|경조사·기타|
|모임 정산|reimbursement|—|—|—|
|문화센터|expense|—|취미·여가|여행·숙박|
|미용|expense|—|쇼핑|뷰티|
|반찬|expense|—|외식|음식점|
|배달|expense|—|외식|배달|
|병원|expense|—|의료·건강|병원·약국|
|뷰티·건강|expense|—|쇼핑|뷰티|
|빵·간식|expense|—|외식|음식점|
|사회보험|fixed_expense|보험·사회보험|—|—|
|생활용품|expense|—|편의점·마트·잡화|생활용품|
|선물|expense|—|쇼핑|패션·잡화|
|세차|expense|—|기타|경조사·기타|
|수영|expense|—|의료·건강|운동·헬스|
|숙박|expense|—|취미·여가|여행·숙박|
|스터디카페|expense|—|기타|교육|
|스포츠 관람|expense|—|취미·여가|스포츠 관람|
|시험 응시료|expense|—|기타|교육|
|실손보험|fixed_expense|보험·사회보험|—|—|
|안경|expense|—|의료·건강|병원·약국|
|알바 급여|income|—|—|—|
|약국|expense|—|의료·건강|병원·약국|
|연금|income|—|—|—|
|연금|savings_out|—|—|—|
|영화/공연|expense|—|취미·여가|영화·공연·전시|
|예금|savings_out|—|—|—|
|온라인 강의|expense|—|기타|교육|
|월세|fixed_expense|주거|—|—|
|의류|expense|—|쇼핑|패션·잡화|
|인터넷|fixed_expense|통신|—|—|
|자녀 캠프|expense|—|기타|교육|
|자녀 학원|expense|—|기타|교육|
|자동차세|fixed_expense|세금|—|—|
|장난감|expense|—|쇼핑|온라인 쇼핑|
|장보기|expense|—|편의점·마트·잡화|마트|
|저녁/외식|expense|—|외식|음식점|
|적금|savings_out|—|—|—|
|전기요금|fixed_expense|공과금|—|—|
|전시|expense|—|취미·여가|영화·공연·전시|
|점심|expense|—|외식|음식점|
|종교|expense|—|기타|경조사·기타|
|주유|expense|—|교통비|주유|
|주차|expense|—|교통비|택시|
|증권|savings_out|—|—|—|
|카페|expense|—|외식|카페|
|코워킹|fixed_expense|구독·멤버십|—|—|
|키즈카페|expense|—|취미·여가|여행·숙박|
|택시|expense|—|교통비|택시|
|통신|fixed_expense|통신|—|—|
|편의점|expense|—|편의점·마트·잡화|편의점|
|프로젝트 대금|income|—|—|—|
|학용품|expense|—|편의점·마트·잡화|생활용품|
|한의원|expense|—|의료·건강|병원·약국|
|헬스장|expense|—|의료·건강|운동·헬스|
|회비|expense|—|기타|경조사·기타|

표의 `fixed_expense`는 `confirm_status != PENDING`이고 고정 그룹 대응표가 정확히 일치할 때의 종류입니다. 고정지출은 예산 봉투·KeyFin 세분류를 만들지 않고 `fixed_group`으로만 집계합니다. `PENDING`이면 고정 그룹을 부여하지 않고 `expense`로 남기며 봉투 통계에서 제외합니다.

## 고정 그룹 정확 매칭

고정 그룹은 원 `subcategory`를 다음 표와 **정확히** 대조해서만 결정합니다. 일치하지 않는 세부분류를 추정해 고정지출로 올리지 않습니다.

|원 세부분류|고정 그룹|
|---|---|
|월세, 관리비|주거|
|전기요금, 가스요금, 수도요금|공과금|
|통신, 인터넷|통신|
|실손보험, 사회보험|보험·사회보험|
|자동차세|세금|
|구독, 코워킹|구독·멤버십|

## 입력 계약

CSV는 기록 필드와 확정 분류 필드만 받습니다. 아래 필수 열이 하나라도 없으면 `MISSING_COLUMNS`입니다.

|구분|열|기본값·처리|
|---|---|---|
|필수|`user_id`, `transaction_id`, `source`|사용자·행 식별자와 입력 출처|
|필수|`transaction_type`, `transaction_date`, `transaction_time`, `status`|종류 판정·시점·취소 상태|
|필수|`category`, `subcategory`, `confirm_status`|확정 분류와 AUTO/CONFIRMED/PENDING 상태|
|필수|`merchant`, `merchant_id`|카드 거래는 필수, 계좌 거래는 빈값 허용|
|필수|`amount_krw`, `account_id`, `card_id`|금액과 채널 식별자. 카드 거래는 `card_id`, 그 외 거래는 `account_id`를 확인|
|선택|`exclude_tag`|없으면 `NONE`. 내부 이체와 예산 반영 여부에만 사용|
|선택(과도기)|`direction`, `payment_method`|없으면 도출. 있으면 `transaction_type`·`card_id`와의 일치 여부만 검증|
|선택|`to_account_id`|없으면 `None`. 타 계좌 이체에만 사용|
|무시|`is_fixed`, `is_recurring`, `spend_pattern`, `classify_source`|있어도 읽지 않는다. 실제 열은 정렬한 `ignored_columns` 메타와 `IGNORED_LABEL_COLUMNS` 경고로 남긴다|

`category`의 상위 카테고리와 원 `subcategory`는 그대로 보존합니다. 반복·고정 판정은 입력 라벨이 아니라 엔진의 반복 규칙, 이 문서의 정확 매칭표, `snapshot.schedules`에서 결정합니다.

## 검토 지점

7봉투에서 주거/보험/공과금의 전용 세분류가 없어 고정지출이 아닌 소비만 `기타 / 경조사·기타`로 명시적으로 접습니다. 주차 비용 등 교통의 세부 불일치는 현재 어댑터의 선택이며 제품 담당자 검토가 필요합니다. 원 세분류는 보존하여 나중에 다시 매핑할 수 있습니다.

등록되지 않은 소비 세분류는 `기타`로 단정하지 않습니다. 먼저 실제 코드의 `CATEGORY_ENVELOPE`에서 원 `category`의 상위 카테고리 fallback으로 **봉투만** 결정하고, 상위 카테고리도 없으면 최종적으로 `기타` 봉투를 사용합니다. 원 세부분류(없으면 `경조사·기타`)는 보존하며 `mapping_fallback`으로 표시합니다. 임의의 LLM 분류는 수행하지 않습니다. `spend_pattern=IMPULSE`는 제공된 라벨의 비중을 보여줄 뿐 인과적 행동 모델의 정답 라벨로 주장하지 않습니다.

## 출처

`source_materials/02_KeyFin_요구사항명세(2).md`의 “예산 봉투 · KeyFin 세분류 초안”, 첨부 CSV 원 분류, 구현 `fdt/mapping.py`·`fdt/ingest.py`.
