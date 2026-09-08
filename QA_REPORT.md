# QA REPORT: KeyFin FDT Workbench v0.2

검증일: 2026-09-07. 대상은 기존 FDT 엔진 v0.1을 포함한 로컬 프론트/API/통합 실행기입니다.

## 1. 결과 요약

| 구분 | 실제 수행 결과 | 증거 |
|---|---|---|
| Python 자동 테스트 | **227 passed**, 81.20초 | `qa/pytest-output.txt`, `qa/pytest.xml` |
| 기존 엔진 회귀 | 기존 테스트 **160개 통과**. 엔진 소스 변경 없음 | `tests/`, `qa/engine-integrity.json` |
| 웹 API | **60개 통과**. 4명×5모드 실제 계산 및 직접 Engine.run과 전체 결과 객체 일치 | `tests_web/test_api.py` |
| 통합 실행기 | **7개 통과**. 실제 서버/수치 자식 프로세스 SIGINT 종료, 포트 재사용, Twin 보존 | `tests_web/test_launcher.py` |
| 브라우저 UI | **18개 체크 통과**, 포착된 uncaught JavaScript 오류 0건 | `qa/ui-report.json`, `qa/ui-test-output.txt` |
| 배포본 압축 해제 점검 | **13개 체크 통과**. 한글·공백 경로에서 별도 실행, 업로드·생성·5모드·종료·재시작 | `qa/package-check.json` |

테스트 환경은 **Linux, Python 3.13.5**입니다. 실제 사용한 런타임 버전은 `requirements-web.lock`에 고정했습니다. 이 릴리스에서 새로 코드 커버리지를 측정하지 않았으므로 이전 엔진의 커버리지 수치를 웹 코드의 결과로 인용하지 않습니다.

## 2. 계산 정합성

소비001~004를 각각 업로드하여 Twin을 만든 뒤 forecast, what_if, goal, risk, optimize를 실행했습니다. 이 20개 조합은 실제 별도 spawn 프로세스를 사용합니다. 웹 결과의 일부 숫자만 확인한 것이 아니라, 같은 저장 Twin에 직접 `Engine.run(request)`를 호출한 **전체 결과 객체와 동등함**을 검사했습니다. 회귀 테스트에는 수치 계산, 카드·거래 처리, 요청 스키마, 재현성 등 기존 엔진 테스트도 포함됩니다.

CSV만으로 만든 Twin의 절대 잔액은 null이며, goal/optimize의 `insufficient_data`를 그대로 반환합니다. UI가 결과를 성공으로 꾸미거나 부족한 계좌 잔액을 0으로 채우지 않습니다. 모델의 `calibrated=false`와 USER_ASSUMPTION 스냅샷 가정도 보존합니다.

## 3. 입력·저장·보안 경계

CSV 중복, 혼합 사용자, 잘못된 헤더, 비UTF-8, 빈 파일, 파일 수 제한, 본문 크기 제한, 파일명 경로 조작을 검사했습니다. JSON 오류는 구조화된 오류로 응답합니다. 미래 스냅샷 등 생성 실패는 영구 Twin 목록에 부분 DB를 커밋하지 않습니다.

원 엔진 스키마와 웹 입력 계약의 일치, 상태 없는 입력, 저장 Twin 삭제·영속성, 토큰·Origin·Host 차단, 정적 파일 헤더를 검사했습니다. 실행 중 작업을 `/api/activity`로 다시 찾는 것도 검사했습니다. 이것은 로컬 도구에 대한 검증이며 보안 침투 테스트나 운영 서비스 인증은 아닙니다.

## 4. 통합 종료

실제 launcher subprocess를 실행하여 다음을 확인했습니다.

- 유휴 상태의 SIGINT 종료 후 같은 포트로 재실행.
- 숫자 작업이 실행 중일 때 프로세스 그룹에 SIGINT 전달, 부모 및 자식 PID 종료 확인.
- 완료된 Twin을 가진 상태에서 optimize 계산 중 종료한 뒤 재실행하여 Twin 유지 확인.
- 같은 데이터 디렉터리의 두 번째 실행 거절, 이미 점유된 포트의 소유권 보존.
- BAT의 CRLF·경로 인용·foreground 실행 구조와 잘못된 포트 입력 처리.

`START.bat`는 별도 백그라운드 셸이나 Node 서버를 만들지 않습니다. 서버 lifespan과 launcher의 finally에서 numeric worker를 정리합니다. 다른 Python 프로세스를 전체 종료하는 방식은 사용하지 않습니다.

**Windows 실제 PC에서 BAT 더블클릭, 콘솔 Ctrl+C 이벤트, 브라우저 자동 실행은 이 환경에서 수행하지 못했습니다.** Windows 지원 분기는 코드/정적 검토 수준이고, 실제 프로세스 종료는 Linux에서 검증했습니다. `docs/WINDOWS_CHECKLIST.md`에 인수 확인 절차를 남겼습니다. 설치 전 가상환경의 최초 pip 다운로드도 이 환경에서는 재현하지 않았습니다.

## 5. 브라우저 UI 검증 방식과 한계

관리형 Chromium은 URL 탐색이 정책으로 차단되어 localhost 페이지를 직접 열지 못했습니다. 테스트용 Chromium 다운로드도 DNS 오류로 실패했습니다. 브라우저 정책을 변경하지 않고, 동일 HTML/CSS/JavaScript를 about:blank에 주입한 뒤 `fetch`를 실제 로컬 서버 HTTP에 연결하는 **테스트 전용 bridge**를 사용했습니다. 배포 앱에는 이 bridge가 적용되지 않으며 정상적인 same-origin HTTP로 실행됩니다.

이 방식으로 실제 DOM 조작과 실제 엔진 결과를 점검했습니다. 다음 18개 체크가 통과했습니다.

1. 6개 페이지 메뉴 렌더링.
2. 한글 파일명 CSV 업로드와 프로필 표시.
3. 현재 상태 간편 폼을 정확한 스냅샷 JSON으로 변환.
4. 업로드 CSV와 수동 상태로 실제 Twin 생성.
5. forecast 폼 수정·실제 계산·5개 결과 탭.
6. what_if 폼 수정·실제 계산·5개 결과 탭.
7. goal 폼 수정·실제 계산·5개 결과 탭.
8. risk 폼 수정·실제 계산·5개 결과 탭.
9. optimize 폼 수정·실제 계산·5개 결과 탭.
10. 입력 변경 후 이전 결과의 재실행 필요 표시.
11. 잘못된 JSON 문법 오류 표시.
12. 서버 스키마 오류 표시.
13. 원문 JSON 직접 편집 후 실제 계산.
14. 요청 JSON 파일 불러오기.
15. UI에서 진행 중 numeric 작업 취소.
16. CSV 이력 전용 목표 계산의 insufficient_data 보존.
17. 390px 화면에서 6페이지 전체 문서의 가로 넘침 없음. 긴 JSON 내부 스크롤은 허용.
18. uncaught JavaScript 오류 0건.

데스크톱 및 모바일 스크린샷을 `qa/ui-*.png`로 보관했습니다. **네이티브 브라우저 HTTP/CSP 적용, OS 클립보드와 실제 파일 저장 대화상자, 브라우저 자동 실행은 bridge 검증에 포함되지 않습니다.** 해당 기능의 구현과 소스 검토는 완료했으나 실제 OS 동작 검증을 대신했다고 주장하지 않습니다. `scripts/ui_smoke.py`의 기본 모드는 네이티브 브라우저로 실행 가능하며, `--bridge`는 이 환경의 제한을 명시하는 별도 QA 옵션입니다.

## 6. 검토 중 수정한 사항

모바일에서 긴 JSON 때문에 카드가 화면 폭을 넘는 문제를 수정했습니다. 선택적 목표 예비자금의 스냅샷 상속, % 입력의 소수 변환, 원문 JSON에서 폼으로 이동하기 전 검증, 실패/취소 후 이전 결과 표시, 새로고침 후 실행 작업 재발견을 점검하고 보완했습니다. 최종 테스트 및 스크린샷은 이 수정 이후의 결과입니다.

## 7. 재현 및 남은 범위

```bash
python -m pip install -r requirements-qa.txt
python -m pytest -q
python launcher.py --no-install --no-browser
# 별도 터미널
python -m playwright install chromium
python -m scripts.ui_smoke --url http://127.0.0.1:8765
```

실제 사용자 금융 데이터에 대한 확률 보정, 운영 배포·인증·암호화·금융망 실행, 고부하 다중 사용자 검증은 범위 밖입니다. Windows 실제 환경 및 최초 설치 확인은 남아 있습니다. 이 릴리스는 **한 사람이 로컬에서 기존 수치 엔진을 검사하는 도구**로 제공됩니다.
