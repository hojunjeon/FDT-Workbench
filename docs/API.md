# Workbench 로컬 API

서버 주소 예시: `http://127.0.0.1:8765`. 모든 예시는 서버가 실행 중인 로컬 환경을 전제로 한다. 외부 LLM 또는 금융망 키는 사용하지 않는다.

## 세션 토큰

GET `/api/config` 응답의 `token`을 후속 쓰기 요청 헤더 `X-Workbench-Token`으로 전달한다. 서버 재시작 시 토큰은 바뀐다. 브라우저 UI가 자동으로 처리한다.

## CSV → Twin → 실행

```python
from pathlib import Path
import time
import httpx

with httpx.Client(base_url="http://127.0.0.1:8765", timeout=30) as client:
    client.headers["X-Workbench-Token"] = client.get("/api/config").json()["token"]
    csv_path = Path("data/demo/consumer_001.csv")
    response = client.post("/api/uploads", files={
        "files": (csv_path.name, csv_path.read_bytes(), "text/csv")
    })
    response.raise_for_status()
    uploaded = response.json()
    # 이력만 사용하려면 snapshot 키를 생략합니다. 아래 값은 데모 가정입니다.
    snapshot = client.get("/api/demo/001/snapshot").json()
    response = client.post("/api/twins", json={
        "upload_id": uploaded["upload_id"],
        "name": "API 테스트 Twin",
        "snapshot": snapshot,
    })
    response.raise_for_status()

    def wait_job(job_id: str) -> dict:
        deadline = time.monotonic() + 310
        while time.monotonic() < deadline:
            r = client.get(f"/api/jobs/{job_id}")
            r.raise_for_status()
            job = r.json()
            if job["status"] == "failed":
                raise RuntimeError(job["error"])
            if job["status"] == "cancelled":
                raise RuntimeError("작업이 취소되었습니다.")
            if job["status"] == "succeeded":
                return job
            time.sleep(0.3)
        raise TimeoutError("작업 조회 시간 초과")

    built = wait_job(response.json()["id"])
    record_id = built["twin"]["id"]
    response = client.post(f"/api/twins/{record_id}/runs", json={
        "mode": "forecast", "horizon_days": 30, "paths": 100, "seed": 42
    })
    response.raise_for_status()
    finished = wait_job(response.json()["id"])
    result = client.get(finished["result_url"]).json()
    print(result["metrics"])
```

## 상태 의미

작업의 `succeeded`는 계산 함수가 반환했다는 뜻이다. 금융 결과의 `status`는 `ok`, `partial`, `insufficient_data`일 수 있다. 예를 들어 이력 전용 Goal은 작업은 succeeded이지만 결과는 insufficient_data이다.

UI 레코드 ID는 저장 폴더를 식별하는 UUID다. 원 엔진의 `twin_id`와 구분한다. 숫자 엔진에 전달되는 Request에는 임의의 UI 필드를 추가하지 않는다.

GET `/api/activity`는 실행 중인 job과 request/record_id를 돌려주며 없으면 `job: null`이다. 새로고침 후 진행 작업 복구에 사용한다. POST `/api/jobs/{id}/cancel`로 현재 작업만 취소한다.

## 오류

```json
{
  "status": "error",
  "error": {
    "code": "SCHEMA_VALIDATION",
    "message": "request: 1 is less than the minimum of 20",
    "details": {"path": ["paths"]}
  }
}
```

예시이며 메시지는 jsonschema 검증 결과에 따라 달라진다. 409 ENGINE_BUSY면 이전 작업을 조회/취소하거나 기다린다. 서버를 강제 종료할 필요가 없다.
