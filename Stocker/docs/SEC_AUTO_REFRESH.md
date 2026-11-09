# 다종목 SEC 공시 자동 수집·갱신

후속 [원문 검토·다종목 재무 보고서](SEC_FACT_REVIEW.md)가 별도 모듈로 연결됐다. 수집기의 자동 값은 계속 미검토 상태이며 새 검토 기록과 공통 재무 입력이 원문 버전·기간·귀속 범위를 확인한다. 기존 수집 작업의 코드·정책은 그대로다.

2026-11-08 구현. 여러 기업의 공시와 표준 재무 수치를 지속해서 보관하는 입력 모듈이다. 기존 BE 원문 검토 보고서, 가격 예측 모델, 과거 평가 작업을 바꾸지 않는다. 월 데이터 예산은 $0이며 외부 LLM 호출과 모델 학습은 없다.

## 목적과 수집 범위

관심 목록의 주식 기호를 SEC 공식 기업 명부와 대조하고 CIK(기업별 SEC 식별번호)로 묶는다. 같은 기업의 여러 주식 종류는 한 번 수집한다. 현재 기호를 확인하기 어려운 기업은 CIK만 지정할 수 있다. 기호·CIK 충돌이나 모호한 기호는 임의로 선택하지 않는다.

10-K(연간 보고서), 10-Q(분기 보고서), 8-K(주요 사건 공시)와 각각의 /A 수정 공시를 추적한다. 최초에는 최신 연간·분기·주요 사건 원문을 받고, 이후 발견한 공시는 대기열로 수집한다. 원문은 실행하지 않는 바이트 파일이다. 수정 공시는 원본과 별도이며, 같은 주소의 내용이 바뀌어도 이전 원본을 남긴다. 최근 목록에서 사라졌다는 이유로 기존 공시를 삭제·철회 처리하지 않는다. 과거 공시의 뒤늦은 발견도 새로운 시장 사건으로 세지 않는다.

submissions API의 최근 목록부터 수집한다. 과거 추가 파일 전체를 받는 역사 자료 수집기는 아니다. 8-K 원문 저장이 사건의 의미·호재·악재까지 판독했다는 뜻은 아니다.

같은 기호라도 구조 변경으로 CIK가 달라질 수 있어 공식 명부의 현재 식별을 따른다. 전신·후신 기업의 다른 CIK 이력은 임의로 합치지 않는다. 필요한 연간·분기 기본 공시가 보관 목록에 없으면 별도 누락으로 표시한다.

```mermaid
flowchart LR
  A[다종목 관심 목록 또는 선정 기업 CIK] --> B[기업 식별 · 중복 제거]
  B --> C[공시 목록 · 표준 재무 · 원문]
  C --> D[요청 제한 · 캐시 · 오류 대기]
  D --> E[원본과 변경 이력 보관]
  E --> F[기간·단위·공시 번호 대조]
  F --> G[미검토 재무 참고자료]
  G --> H[후속 원문 대조 · 투자 보고서 연결]
```

## 재무 계산과 보류 기준

`config/sec_fact_policy.json`이 표준 미국 회계 항목(us-gaap)의 매출·영업이익·순이익·현금·자산·부채·영업 현금흐름·설비 취득 지출·희석 주당순이익을 정의한다. **공시 번호, 회계기간 끝, 공시 형태, 통화·단위**가 맞아야 사용한다. 금액은 Decimal로 계산하고 JSON에는 문자열로 저장한다.

| 항목 | 허용 기간과 계산 |
|---|---|
| 분기 손익·주당순이익 | 10-Q의 해당 분기 60~120일 |
| 연간 손익·현금흐름 | 10-K의 330~400일 |
| 분기 보고서의 현금흐름 | 가장 긴 적합한 60~330일 구간, **연초부터 누적**으로 표시 |
| 현금·자산·부채 | 결산일 현재 |
| 영업·순이익률 | 같은 공시·시작일·종료일·단위의 이익 ÷ 양수 매출 × 100 |
| 잉여현금흐름 | 같은 기간 영업 현금흐름 − 음수가 아닌 설비 취득 지출 |

매출의 여러 표준 항목은 더하지 않고, 값이나 기간이 충돌하면 보류한다. 누적액을 다른 공시에서 임의로 빼지 않는다. 수정 공시가 연결되면 적용 범위를 검토할 때까지 파생 비율·잉여현금흐름을 보류한다. 누락은 0으로 채우지 않는다.

기업 고유 확장 회계 항목, IFRS, 비달러 수치, 20-F/6-K와 8-K 사건 해석은 자동 정규화 범위 밖이다. 일반 영업기업으로 확인되지 않은 기업에는 영업기업 재무 계산을 적용하지 않는다. 자동 결과는 항상 `not_reviewed`다. 기존 BE의 사람이 확인한 선택 행을 대체하거나 검토 완료로 바꾸지 않는다. 목표가·매력도·추천 점수는 산출하지 않는다.

## 저장과 이용 가능 시점

- `reports/private/sec_raw/real/`: 원본 바이트, 주소·해시·최초 저장 시각, 재확인 기록.
- `local_data/sec_http/real/`: 캐시, 요청 간격·일일 사용량·대기 요구. 연락처 헤더는 저장하지 않는다.
- `reports/private/sec_store/real/entities/<CIK>/`: 공시 목록·대기열, 불변 스냅샷과 이용 가능 시각, 복구 가능한 최신 포인터.
- `reports/private/sec_store/real/runs/`: 실행 당시 관심 목록, 해시, 기업별 결과·한국어 상태 보고서.
- `reports/private/sec_context/real/`: 원문 링크·기간·단위·보류 사유를 담은 재무 참고자료.
- `reports/private/sec_refresh_jobs/<id>/`: 코드·정책 해시를 고정한 작업, 상태·Windows 등록 정보.

실제 자료와 가상 시험은 `real`/`illustrative`로 분리한다. `available_at`은 불변 스냅샷을 저장한 **이후**의 로컬 이용 가능 시점이다. 결산일·공시 접수일로 되돌리지 않는다. 수정·되돌림은 새 시점으로 남기며 원본·스냅샷 해시를 조회 때 다시 검사한다. 스냅샷→이용 가능 기록→최신 포인터 사이에서 중단돼도 덮어쓰지 않고 복구한다. 시계가 뒤로 가면 새 이용 가능 시점을 만들지 않는다.

이는 오늘부터 축적하는 시점 보존이다. 오늘 companyfacts 값이 당시 원본과 동일했는지 검증한 역사 자료가 아니다. 진행 중인 가격 과거 평가와 잠긴 최종 시험에 현재 재무를 소급해서 추가하지 않는다.

## 실행과 다종목 확대

Stocker 폴더에서 실행한다. `SEC_USER_AGENT`에는 프로그램 이름과 실제 연락 가능한 이메일을 포함한 영문 식별 문자열을 Windows 사용자 환경변수로 저장한다. 끝의 `T`가 필요하다. 값은 채팅·저장소에 넣지 않는다. 작업은 실행 때 사용자 변수를 다시 읽으며 `.env`를 자동 로드하지 않는다. 별도 API 키나 유료 가입은 필요하지 않다.

```powershell
.\.venv\Scripts\python.exe -B -m research.sec_refresh --check-config
.\.venv\Scripts\python.exe -B -m research.sec_refresh
.\.venv\Scripts\python.exe -B -m research.sec_refresh --symbols BE MSFT XOM
.\.venv\Scripts\python.exe -B -m research.sec_refresh --ciks 1664703 789019
.\.venv\Scripts\python.exe -B -m research.sec_refresh --ciks 1664703 --as-of 2026-11-08T14:00:00Z
.\.venv\Scripts\python.exe -B -m research.sec_refresh_demo
```

기본 `config/sec_watchlist.json`의 BE/MSFT/XOM은 연결 확인용이며 추천이나 대표 표본이 아니다. 최대 1,500개 항목의 목록을 받을 수 있다. 목록 변경은 허용하되 실행마다 원본을 보관한다. 산업별 100개 기업의 실제 수집 완료는 별도다.

선정 기업이 고정돼 `cohort.json`이 생기면 `selected`에서 CIK·표시 이름만 새 목록으로 복사할 수 있다. 가격·수익률을 사용하거나 구간을 바꾸지 않으며 기존 파일을 덮어쓰지 않는다.

```powershell
.\.venv\Scripts\python.exe -B -m research.sec_refresh --import-cohort reports/private/historical_panel/<id>/cohort.json --output-watchlist config/sec_watchlist_panel.json
.\.venv\Scripts\python.exe -B -m research.sec_refresh --watchlist config/sec_watchlist_panel.json --prepare-job
```

## 예약 실행과 운영 한도

```powershell
.\.venv\Scripts\python.exe -B -m research.sec_refresh --prepare-job
powershell -NoProfile -File scripts/install_sec_refresh_task.ps1 -JobFile reports/private/sec_refresh_jobs/<id>/job.json
.\.venv\Scripts\python.exe -B -m research.sec_refresh --job reports/private/sec_refresh_jobs/<id>/job.json --check
.\.venv\Scripts\python.exe -B -m research.sec_refresh --job reports/private/sec_refresh_jobs/<id>/job.json --status
```

`Stocker-SEC-Refresh-<id>`는 15분 간격과 로그인 때 창 없는 Python으로 실행한다. 현재 사용자·제한된 권한이며 비밀번호를 보관하지 않는다. 중복을 막고 놓친 실행은 컴퓨터가 사용 가능해지면 시작한다. IDE를 닫아도 유지된다. 전원이 꺼진 동안에는 실행하지 못하며 현재 설정은 로그인 세션이 필요하다.

한 번에 최대 100개 기업·300회 요청·20분, 기업당 원문 최대 3개로 제한하고 다음 순번을 보존한다. 캐시 유효기간은 공시 목록 1시간, 기업 명부·재무·원문 24시간이다. 새 재무 공시 발견 시 재무를 먼저 확인한다. 숫자가 늦게 도착하면 처음 6회는 15분 후, 이후 24시간 후 재확인한다. 이는 **방문한 기업의 재확인 기준**이다. 1,200개 기업을 한 번 도는 데 적어도 12회 실행이 필요하며 요청·다운로드 한도로 더 길어질 수 있다. 모든 종목의 실시간 감시를 보장하지 않는다.

작업 간 공유 잠금으로 요청마다 1초 이상 간격을 둔다. 하루 최대 50,000회·512MiB 다운로드, 원문 저장 5GiB 상한·최소 디스크 여유 1GiB다. 정규화 버전·보고서 용량은 원문과 별도다. 상한에서 자동 삭제하지 않는다. 403/429/503·정책 차단 응답은 Retry-After 또는 최소 15분 기다리며 주소·프록시로 우회하지 않는다. 오류가 나도 저장한 자료는 남는다.

환경변수가 없으면 요청 0회로 설정 대기 상태를 남긴다. 코드·정책 해시가 바뀌면 기존 작업은 중단하며 새 검증·작업이 필요하다. 관심 목록 내용은 해시 고정 대상이 아니며 실행별 기록 대상이다. 기존 뉴스·가격 예약 작업은 그대로 유지한다.

## 검증과 다음 단계

`tests/test_sec_refresh.py`와 `examples/sec_refresh_golden.json`은 여러 기업·중복 주식·1,200개 식별, 잘못된 CIK·기간·단위, 누적 현금흐름, 수정·되돌림·과거 조회, 캐시·오류 대기·요청 예산, 중단 복구와 작업 해시를 가상 입력으로 검사한다. 이는 실제 재무 해석이나 예측 수익성 검증이 아니다.

BE/MSFT/XOM 실제 연결은 2026-11-08T12:57:37Z 실행에서 요청 15회로 확인했다. 금액이 원문과 의미까지 일치하는지는 별도 검토가 필요하다. 다음은 누락·수정 공시를 원문과 대조하고 검토 이력을 만든 뒤 일반 종목 투자 보고서 입력에 연결하는 단계다. 3개 기업을 전체 산업의 대표성이나 투자 성능 증거로 사용하지 않는다.

완료 확인: 신규 SEC 시험 81개를 포함한 전체 490개 통과. 2026-11-08T13:05:39Z 실제 반복 수집은 요청 0회였다. `Stocker-SEC-Refresh-0800956935094918` 등록 후 22:07:43 KST 백그라운드 실행이 종료 코드 0으로 끝났으며 3개 기업·요청 0회를 기록했다. 다음 실행은 22:22:43 KST로 확인했다. 이는 기록 당시 상태이며 이후 상태는 작업의 `status.json`과 Windows 스케줄러를 확인한다. 기존 BE 관찰·과거 패널 작업의 해시도 모두 유효했다.

공식 근거: [SEC API 설명](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [접근 정책·요청자 식별·요청 제한](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data).
