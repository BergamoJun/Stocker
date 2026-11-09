# 기업별 재무 누락 보완

`financial_fill`은 저장된 SEC 원문의 숫자를 기업·기간·단위·표 행·회계 분류까지 대조하는 별도 입력 어댑터다. 기존 검토 기록이나 재무 보고서를 수정하지 않고, 부족한 항목만 새 보완 기록에 저장한다. 네트워크 요청과 외부 AI 호출은 없다.

BE/MSFT의 확인된 항목은 그대로 사용한다. 표준 태그와 행 제목이 명확한 경우만 자동 대조하며, 경쟁 값·검토 충돌·오래된 검토·수정 공시가 있으면 보류한다. 미해결 검토를 넘어설 수 있는 예외는 정책에 명시한 XOM 매출 하나다.

XOM은 같은 `us-gaap:Revenues` 태그로 영업매출, 관계회사 이익, 기타이익, 합계를 표시한다. 제품·서비스 축의 정확한 회사 전용 멤버와 각 행을 구분하고 `영업매출 + 관계회사 이익 + 기타이익 = 총수익`이 원문에서 정확히 맞아야 영업매출만 채운다. 사업부·지역 등 다른 차원을 섞지 않는다. 결과의 범위는 `consolidated_sales_excluding_equity_affiliate_and_other_income`이며 기존의 합계 수익과 구분한다. [확인한 SEC 원문](https://www.sec.gov/Archives/edgar/data/2115436/000211543626000020/xom-20260930.htm).

영업손익을 세전이익이나 부문이익으로 대체하지 않는다. 같은 CIK의 연간 자료가 없으면 다른 CIK의 자료를 합치지 않으며 전분기·최근 12개월 공백을 유지한다. 누락을 0으로 바꾸거나 나머지 항목만으로 전체가 완성됐다고 표시하지 않는다.

원문 숫자 ID·DOM/문맥/행 위치·해시·실제 보완 수신 시각·정의·합계 검증에 사용한 숫자 ID를 보관한다. `deterministic_source_match`는 고정 규칙으로 원문과 맞췄다는 뜻이며 사람 승인·회계감사·예측 성능 검증이 아니다. 기존 `recorded_source_review`와 별도로 집계한다. 공통 계약의 과거 이름 `coverage.reviewed_financial_metrics`는 연결된 두 종류의 수치를 합친 수이며, 구체적 구분은 각 수치의 `validation_kind`, `LOCAL_INPUT.financial_coverage`와 보완 기록의 `payload.coverage`를 따른다.

```powershell
.\.venv\Scripts\python.exe -B -m extensions.financial_fill --baseline <baseline> --ciks 1664703 789019 2115436
.\.venv\Scripts\python.exe -B -m extensions.analysis_contract --baseline <baseline> --ciks 2115436 --market <market-folder> --financial <financial-folder>
```

보완 기록을 만든 다음 초부터 공통 입력에 연결한다. 원문·검토·수정 목록·연속 버전이 바뀌면 새 보완 기록을 생성해야 한다. 이전 보고서는 원래 기준시각으로 재생한다. 파생 가격·목표가·추천 점수·훈련 입력은 추가하지 않는다.
