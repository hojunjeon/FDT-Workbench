# KeyFin FDT Engine v0.1

**소비 CSV로 만들고, 5개 모드를 직접 선택하여 JSON 수치·표·시각화 명세를 받는 독립 엔진입니다.**
LLM·자연어 라우터·앱·실제 금융 거래 실행은 포함하지 않습니다.

먼저 볼 파일: **SPEC.md → PLAN.md → QA_REPORT.md**.
구현은 `fdt/`, 직접 실행할 예제는 `examples/`, 실제 실행 결과는 `artifacts/`에 있습니다.

## 1. 바로 실행하기

프로젝트 최상위 디렉터리에서 Python 3.11 이상을 사용합니다.
검증한 환경과 정확한 버전은 `QA_REPORT.md`, `requirements-test.lock`을 보세요.

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell에서는 .venv\Scripts\Activate.ps1

python -m pip install -e ".[dev]"
python -m fdt list-modes
```

최초 의존성 설치에는 인터넷 또는 준비된 wheel이 필요합니다.
**설치 후 엔진 실행은 API 키·네트워크·GPU 없이 가능합니다.**

### A. CSV만으로 Twin 만들기

```bash
python -m fdt build --csv data/demo/consumer_001.csv --db work/history.sqlite --out work/history_summary.json
python -m fdt run --db work/history.sqlite --mode forecast --horizon-days 30 --out work/history_forecast.json
```

CSV는 거래 이력이지 현재 잔액 명세가 아닙니다. 이 실행은 상대적인 자금 여력 변화와
소비 분포를 계산하며, 현재/미래 현금 잔액은 **null**입니다. 오류가 아닌 의도된 처리입니다.

### B. 명시적 데모 잔액을 추가해 5모드 모두 계산하기

```bash
python -m fdt build --csv data/demo/consumer_001.csv --snapshot examples/snapshot_001.json --db work/demo.sqlite --out work/twin.json

# 아래에서 직접 모드를 고릅니다.
python -m fdt run --db work/demo.sqlite --request examples/requests/forecast.json --out work/forecast.json
python -m fdt run --db work/demo.sqlite --request examples/requests/what_if.json --out work/what_if.json
python -m fdt run --db work/demo.sqlite --request examples/requests/goal.json --out work/goal.json
python -m fdt run --db work/demo.sqlite --request examples/requests/risk.json --out work/risk.json
python -m fdt run --db work/demo.sqlite --request examples/requests/optimize.json --out work/optimize.json
```

`build`는 기존 DB를 덮어쓰지 않습니다. 재생성 시 새 DB 파일명을 사용하세요.
`examples/snapshot_*.json`의 잔액·카드 종류·미결제액·목표·예산은 **USER_ASSUMPTION 데모 가정**입니다.
CSV에서 추론하거나 실제 사용자 잔액으로 확인한 값이 아닙니다.

|모드|무엇을 계산하는가|주요 시각화|
|---|---|---|
|forecast|일별 소비·자금 여력·현금/카드 미결제액 분포|P10/P50/P90 band와 중앙선|
|what_if|같은 난수 경로에서 기준안과 변경안의 차이|기준/분기 선, 경로별 차이 표|
|goal|목표일 가용 현금과 목표 성공/기간 중 부족 확률|목표선, 만기 분포, 부족액 표|
|risk|총/개별계좌 부족, 봉투 초과, 충격 시나리오|부족 확률, 계좌 막대, 결제 일정|
|optimize|명시한 감축 후보 중 제약을 만족하는 최소 변경|후보 scatter, 가능/불가 및 선택 표|

## 2. Python 라이브러리에서 사용

```python
import json
from pathlib import Path
from fdt import Twin, Engine

snapshot = json.loads(Path("examples/snapshot_001.json").read_text(encoding="utf-8"))
twin = Twin.from_csv("data/demo/consumer_001.csv", snapshot=snapshot)
engine = Engine(twin)

result = engine.run({
    "mode": "what_if",
    "horizon_days": 90,
    "paths": 400,
    "seed": 42,
    "scenario": {"expense_reductions": {"외식": 0.2, "쇼핑": 0.1}},
})
print(result["metrics"]["paired_terminal_cash_delta_p50_krw"])
print(result["visualizations"])
```

자연어 질문은 여기서 처리하지 않습니다. 외부 Agent가 정형 request로 라우팅한 뒤
이 인터페이스를 호출하면 됩니다. 상세 계약은 `docs/INTEGRATION.md`에 있습니다.

## 3. 결과 구조

`schema_version / mode / status / twin_id / revision / as_of / model / input_digest`
뒤에 다음 데이터가 옵니다.

- `metrics`: 숫자 또는 null, 단위, 계산 방식, 근거 참조.
- `datasets`: 일별 경로·봉투·일정·목표·위험·최적화 후보 등 행 배열.
- `visualizations`: 어느 dataset의 어떤 field로 선·band·막대·scatter·표를 만들지 명시.
- `assumptions / warnings / limitations`: 데이터 부족과 모델 가정.
- `decision`: 목표/탐색 결과. 불가능하면 infeasible, 미상은 unknown. 실행은 하지 않음.

`ok`는 입력으로 해당 계산이 가능하다는 의미입니다. 예측 정확도가 검증되었다는 의미가 아닙니다.
`partial`은 상대 분석만 가능한 상태, `insufficient_data`는 잔액이 필요한 판정을 보류한 상태입니다.
출력의 P10/P90는 모델 분위수이며 실세계 보장 구간이나 확정적인 미래 값이 아닙니다.

## 4. 4개 데이터 × 5모드 실행과 시각화 확인

```bash
python -m scripts.run_demo
python -m scripts.render_report --result artifacts/demo/001/forecast.json --out work/forecast.html
python -m scripts.render_report --result artifacts/demo/001/optimize.json --out work/optimize.html
```

생성한 HTML을 브라우저로 열면 됩니다. 서버·CDN·외부 스크립트가 없는 검토용 어댑터입니다.
이 파일은 사용자 앱/챗봇이 아니라 엔진 output 사용 예시입니다.
미리 만든 결과: `artifacts/demo/001` ~ `004`, `artifacts/forecast_preview.html`,
`artifacts/optimization_preview.html`.

## 5. 데이터와 현재 상태

각 CSV는 한 명만 포함합니다. 파일 번호를 user_id로 추정하지 않습니다.

|파일|포함된 실제 ID|행 수|데모 초기 관리계좌 잔액|데모 카드 정책|
|---|---|---:|---:|---|
|consumer_001.csv|USR-DEMO-002|337|3,000,000원|신용·미결제액 0·주간 발행 + 2일|
|consumer_002.csv|USR-DEMO-003|193|900,000원|체크|
|consumer_003.csv|USR-DEMO-004|262|5,000,000원|신용·미결제액 0·주간 발행 + 2일|
|consumer_004.csv|USR-DEMO-005|188|2,000,000원|체크|

위 잔액·카드 정책은 전부 테스트용으로 별도 설정했습니다. 입력 파일의 관측 사실이 아닙니다.
003의 투자 1,200만원/부채 1억원도 자산 shock 예시용 가정이며 원리금 납부액에서 역산한 값이 아닙니다.
현재 순자산은 전체 자산/부채 보고가 확인되지 않으면 null입니다.

스냅샷을 직접 입력할 때는 `fdt/schemas/snapshot.json`을 기준으로 합니다.
모르는 계좌 잔액을 0으로 넣지 마세요. 알려진 계좌만 넣으면 누락 채널에 대해 부분 결과를 반환합니다.
현금 잔액이 충분해도 연결 결제계좌가 부족할 수 있으므로 총계좌/개별계좌 위험을 구분합니다.

## 6. 이벤트 갱신

```bash
python -m fdt update --db work/demo.sqlite --events examples/events_001.json --expected-revision 0 --out work/updated.json
```

예제는 새로운 LIVE 거래와 그 이후의 LIVE 스냅샷을 함께 보냅니다. 파일 내부는
**인터페이스 테스트용 fixture**이지 은행에서 수집한 LIVE 데이터가 아닙니다.

같은 event_id와 내용은 재처리하지 않습니다. 다른 내용으로 같은 ID를 보내면 충돌합니다.
거래/취소가 추가되면 같은 날짜여도 기존 snapshot을 dirty로 표시합니다.
새 관측 스냅샷이 없으면 잔액을 임의로 업데이트하지 않고 절대 잔액 분석을 보류합니다.
DB의 최신 revision은 inspect로 확인합니다. CAS로 낡은 writer를 거부합니다.

## 7. 검증 다시 실행

```bash
python -m pytest -q --cov=fdt --cov-report=term-missing
python -m scripts.backtest
python -m compileall -q fdt scripts
python -m scripts.release_smoke
```

백테스트는 사용자별 2개 cutoff, 이후 14일, 총 8개 합성 데이터 구간입니다.
실제 잔액 관측이 없어 잔액 예측의 정확도/연체 확률 보정을 검증했다고 주장하지 않습니다.
보고서는 `QA_REPORT.md`, 원시 기록은 `artifacts/`에 있습니다.

정확한 패키지 재현:

```bash
python -m pip install -r requirements.lock
python -m pip install -r requirements-test.lock
python -m pip install --no-deps --no-build-isolation -e .
```

같은 seed만으로 다른 NumPy/플랫폼 버전의 완전 동일 결과를 보장하지 않습니다.

## 8. 범위와 운영상 주의

이 버전은 **개인 현금흐름 중심 FDT**입니다. 투자/부채 잔액은 입력받아 표시하고
명시적 투자 평가액 shock은 계산합니다. 투자 수익률 추정·포트폴리오 covariance·대출 상환표·
보험 보장 분석은 구현하지 않았으며, CSV에서 그러한 사실을 만들어내지 않습니다.
블록 bootstrap은 일회성 등록금·선물 같은 이벤트도 재표본추출할 수 있습니다.
장기/계절성·개별 계약·행동의 인과 효과가 필요한 곳에서는 별도 모델과 데이터가 필요합니다.

SQLite와 예제 CSV는 **평문 개발용 저장물**입니다. 인증·권한·암호화·동의 관리·
실제 금융망 관측 진위 검증은 운영 호스트/adapter가 책임져야 합니다.
제공받은 더미 자료 외의 실제 개인정보를 공개 저장소에 올리지 마세요.

최적화는 유한 후보 안에서만 최적이며, '전 금융생활의 최적 행동'을 보장하지 않습니다.
금융망 API 호출·실제 이체·결제·계약 변경 기능은 코드에 없습니다.
