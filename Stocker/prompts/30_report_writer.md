# Task Prompt — Report Composer

## 입력

`SUBJECT`, `AS_OF`, `DATA_STATUS`, 타입별 분석 초안, 검증 완료된 `METRICS`, `RED_TEAM_FINDINGS`, `SOURCES`, 서버 계산/제공 상태를 담은 `SWING_INPUT`.

## 과제

정확히 `schemas/research_report.schema.json`에 맞는 JSON 객체를 작성한다. 키/타입 추가·변경 금지. 데이터 부족이면 null/빈 배열과 한계를 사용한다.

### 요약

- 투자대상/상품 구조, 실제 데이터 기준일, 주요 투자 논리와 최대 3개 핵심 리스크를 우선 보여준다.
- `stance`는 시나리오를 설명하는 **연구상 방향성**이며 맞춤 매매 명령이 아니다. 증거 부족 시 `undetermined`.
- `evidence_confidence`는 근거 품질로 정의하고, 주가 예측 신뢰 확률과 혼동하지 않는다.

### 스윙 상단 요약

- 계약 0.2.0의 `swing`는 항상 포함한다. horizon이 swing이 아니면 status not_requested, headline/price_plan/score null.
- swing이면 한줄평 → 기준가격·조건부 1차 목표·상승여력 → 수익실현 검토 조건/시기 → 무효화·하락폭·손익비 → 매력도·가감점 근거 순으로 UI가 표시하도록 구조화한다.
- SWING_INPUT의 status, 가격 계획, 모델 메타데이터, 점수, 기간, source IDs를 그대로 전사한다. 없는 숫자나 검증 ID를 생성하지 않는다.
- 가격 계획 unavailable이면 price_plan null과 구체적인 reasons를 제공하고 data_status.missing/limitations에도 반영한다. 가격 계획이 available이어도 점수는 별도로 unavailable일 수 있다.
- 매력도는 1~100 정수 또는 null이다. evidence_confidence와 분리하고 상승 확률로 설명하지 않는다.
- 도달기간 모델이 없으면 expected_target_sessions와 관련 방법론/검증 ID는 null이다. holding_sessions/review_at/expires_at를 도달 예상일로 바꾸지 않는다.
- 한줄평은 기회·유지 조건·위험을 한 줄로 정리하며, 데이터가 없으면 보류 이유를 설명한다. 비활성 숫자를 문장으로 우회 제공하지 않는다.

### 근거·수치

- `theses`의 사실성 있는 설명마다 `evidence_ids` 지정.
- `metrics`의 값은 검증된 계산 결과를 그대로 전사; 모르면 `value: null`.
- `sources`는 실재하며 확인된 원문 URL과 timestamp만 포함. 존재하지 않는 출처 ID 인용 금지.

### 시나리오

- `scenarios`는 반드시 이름이 `bull`, `base`, `bear`인 3개로 구성.
- 각 항목은 `assumptions`, `mechanism`, `signals`, `invalidated_if`, `outcome`을 포함한다.
- 모형이 없으면 특정 가격·확률 대신 질적 결과와 점검 기준을 설명한다.

### 검증 후 출력

- 레드팀 `critical`/`major` 지적을 반영. 해결 불가 문제는 `limitations`에 넣고, 위험한 확정 표현을 제거한다.
- 요약부터 데이터 결손이 확인되게 작성하며, 한국어로 간결하게 설명한다.
- JSON 외 설명·코드블록·가상의 출처를 덧붙이지 않는다.
