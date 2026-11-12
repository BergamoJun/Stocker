# SEC EvidenceAdapter v1.1 — 의미·관계·계산 적격성

현재 강화 경로는 `python -m extensions.sec_evidence_v1_1`이다. 역사 재생에
고정된 `sec_evidence_v1`, SEC 수집기, ImpactEngine v1 산식·Schema는 변경하지
않는다. v1을 새 승인에 사용하면 기존 결함이 남는다. v1은 역사 참조 경로이며
모든 소비자의 전환을 자동으로 수행하거나 기존 예약 작업을 변경하지 않는다.

## 호환성

원래 v1의 source/후보/TERMS/manifest/receipt/owner trust 입력 계약을 재사용한다.
기존 receipt는 덮어쓰지 않는다. `reviews.store`는 원래 구조·출처 검사에 새 의미
검사를 추가한다. 과거 v1에서 저장된 receipt도 승격 전에 원문 의미를 재검증한다.
새 결과는 닫힌 `sec-evidence-adapter-v1.1` Schema로 구분한다. 원래 v1 보고서의
방법 해시·검증기·파일은 그대로 재생한다. 의미 필드를 입력에 덧붙여 자체 승인할
수 없으며, `SEMANTIC_RULE_VALIDATED`는 검증기가 원문에서 도출한 출력이다.

## 검증 계층

1. v1의 CIK·원본 hash·incarnation·정확 span·통화·native IXBRL 숫자·기간 검사.
2. UTF-8 원본을 기존 parser로 다시 읽고 `<p>` 경계와 원문/파생문 지도를 고정.
   지원하는 완전한 문장을 typed observation으로 파싱한다. 단어가 가깝다는 이유로
   계약을 연결하지 않고 script/style/hidden 내용을 사용하지 않는다.
3. 명시적 계약 ID·native 원금 span·금리 index·통화·원금/부분 비율·reset 의미·
   헤지 상품/대상/방향/실제 covered principal/비율·기간을 교차 검증한다.
4. 서로 다른 두 검토자·일치하는 terms·owner allowlist·최초 가용 시각·현재
   incarnation이 모두 필요하다. 두 명의 동의가 빠진 원문 관계를 채우지는 못한다.
5. 분석 cutoff에서 유효하고 요청 기간 전체를 덮는 경우만 `verified_exposures`에
   포함한다. `historical_evidence`는 원문 사실이 검증된 역사 자료로, 계산 적격성과
   다르다. 과거 cutoff의 정상 계약은 허용하고 만료·미래 효력·부분 겹침은 보류한다.
   현재 미효력 계약의 미래 조건부 활성화, 자동 부분 기간 비례는 지원하지 않는다.

`bridge.calculate`는 결과의 seal만 신뢰하지 않고 같은 source·trust·기간으로
승격과 의미 증거를 재생한다. 현재 적격 목록만 기존 Impact 입력으로 변환한다.
분석 기간이나 event effective를 바꾼 입력은 기존 적격성을 재사용할 수 없다.

## 지원 문장 범위

일반 SEC 계약 언어 전체를 해석하는 모델이 아니다. 다음과 같은 명시적인
영문 단락 문법만 자동 확인한다. 아래는 **ILLUSTRATIVE — NOT LIVE DATA**다.
완전한 양성 예제의 native IXBRL 삽입과 각 필드 witness는 테스트에 있다.

```text
Loan TEST_LOAN in USD has floating-rate principal USD 1000000000 indexed to SOFR; repricing fraction 1; policy reference ILLUSTRATIVE_USD_POLICY_RATE maps one-for-one to SOFR with coefficient 1; supported delta -100 to 100 bp; ACT/365F-v1; constant principal; interest is expensed pretax.
Loan TEST_LOAN (USD, SOFR): The applicable interest rate resets on November 9, 2026.
Loan TEST_LOAN is effective from 2026-11-08 through 2027-11-10.
Loan TEST_LOAN has no interest rate hedges during 2026-11-08 through 2027-11-10.
```

원금은 위 계약 단락 안의 native IXBRL 원금 값이어야 한다. 별도 표의 같은
숫자를 붙이지 않는다. `January 1 of each year`도 지원하지만 실제 요청에 맞는
첫 reset occurrence를 제출해야 한다. 만기/지급/보고일은 reset 근거가 아니며,
같은 날짜에 만기와 reset이 모두 명시된 경우는 별도 reset 근거로 허용한다.

단순 선형 헤지의 허용 예제는 다음과 같다.

```text
Swap HEDGE_1 hedges Loan TEST_LOAN: receive-floating SOFR and pay-fixed; USD; notional USD 500000000; hedged principal USD 500000000 of repricing principal USD 1000000000; coverage fraction 0.5; fixed offset coefficient 1; effective from 2026-11-08 through 2027-11-10; no basis risk, caps, floors, or optionality.
```

단순 notional/총부채 비율은 사용하지 않는다. 명목금액과 명시적 covered
principal이 같고, 동일 index·통화·기간에 receive-floating/pay-fixed 방향의
선형 1:1 상쇄임이 모두 명시된 범위만 지원한다. 명시적 비율은 Fraction으로
정확히 대조하며 Impact의 Decimal 산식은 변경하지 않는다.

기존의 포괄적인 “floating loan / no hedge / reset date” 가상 문단은 계약과
원금·지표·기간을 이 범위로 결합하지 못하므로 새 경로에서는 보류될 수 있다.
이는 의도된 검증 계약 강화다. 원래 테스트/예제는 v1 경로에서 보존하고,
명시적 reset·hedge 양성 대조군으로 무조건 거절 전략이 아님을 검사한다.

범위 밖의 서술, 조각난 여러 표/문서에 걸친 관계, 복합/옵션부/다중 지표 스왑,
환율/통화 간 상쇄, cap/floor·basis risk·변동 원금·확률적 상쇄는
`HUMAN_REVIEW_REQUIRED`/`INSUFFICIENT_EVIDENCE` 또는 `DEFERRED`다. 사람이
체크박스를 채워 이 규칙을 우회할 수 없으며, 새로운 지원 구조는 별도 검증과
방법 버전이 필요하다. 실제 계약의 일반 의미 정확도는 `NOT_MEASURED`다.

## 실행과 격리

```powershell
.\.venv\Scripts\python.exe -m extensions.sec_evidence_v1_1 --tickers BE MSFT XOM --as-of <UTC> --source-store <private-snapshot> --legacy-review-root <private-review-copy> --review-store <private-contract-copy> --out <private-output>
.\.venv\Scripts\python.exe -m extensions.sec_evidence_v1_1 --validate <report-folder>
```

네트워크 수집이 없고 항상 fresh parse로 공유 캐시를 쓰지 않는다. 실제 trust는
`--trust-policy`와 `--trust-sha256`으로 고정하며 real nonempty trust 재생은 외부
owner policy가 일치해야 한다. `--event-input`은 event-only 원래 Impact Schema다.
새 source·review snapshot과 해시를 고정해 운영 mutable 상태와 분리한다. 원본
예약 작업을 실행·중단·재등록하거나 바뀐 운영 포인터를 복원하지 않는다.

SHADOW, null 점수/목표가/계획을 유지한다. 실제 독립 검토 0건을 완료로
표시하지 않고, 가상 검토·소프트웨어 테스트를 실제 시장 검증으로 집계하지 않는다.
