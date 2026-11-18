# Personal Research v5.1 — Paired SEC Research Synthesis

현재 결과 계약은 personal-research-v5.1-r1이다. 최초 실험 v5.1 소스20개를 v51/에
보존했다. r1은 메모에서 실제 추론 여부와 수락/보완 역할을 명시하는 표시 변경이다.
프롬프트·스키마·검증기·실험 결과는 변경하지 않으며 모델을 다시 호출하지 않는다.

P5.1은 인용 복사·ID 후보 구성·수치·표 작성을 Python으로 처리하고, 모델에는 역할별
사업/위험+재무 근거 쌍의 조건부 경제적 해석만 맡긴다. 원문 전체 문단과 회계기간·
범위·단위·식별자를 유지하고 중복을 제거한다. 후보 관계는 CANDIDATE_RELATIONSHIP이며
경제적 인과관계를 입증한 것으로 표시하지 않는다. 대상 기업의 사업 anchor를 실제
SEC 자료에서 선택한다. XOM의 재무 이력 부족과 ETF는 부족한 분석을 억지로 생성하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT --memo --ai --offline
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MU --memo --ai --ai-mode integrated --offline
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MU --memo --ai --ai-mode selective --offline
```

roles는 최대3회, integrated는1회에 세 역할, selective는 핵심 논거/반론을 만든 뒤
유효한 핵심 해석과 추가 현금/회계기간 불확실성이 있을 때만 모니터링을 요청한다.
동일 pinned Qwen/CUDA B를 사용하고 출력416~960토큰/요청, 문맥4096, 예비 overhead192를
유지한다. 예산은 근거 coverage와 cash/fiscal uncertainty에 따라 바뀌며960을 넘지 않는다.
입력은 최대2688토큰(coverage 부족 시1856)과 context−output−overhead 중 작은 값이다.
원문을 의미가 끊기도록 자르지 않는다. 예산을 넘거나 tokenizer가 없으면 추론을 보류한다.

실제 모델의 `/apply-template`와 `/tokenize`로 생성 전에 입력 토큰을 측정한다.
두 endpoint는 토큰 생성 없이 동일한 인증 loopback 서버만 사용한다. 실제 provider의
입력/출력 usage와 사전 측정을 함께 기록한다. 문자 수를 토큰 수로 바꾸지 않는다.
[pinned llama.cpp API 문서](https://github.com/ggml-org/llama.cpp/blob/b12110/tools/server/README.md)

원래 P5 검증기는 변경하지 않았다. 새 역할별 구조를 기존 P5의 금지 주장/수치 검사로
대조하고 원문 위치/기업/해시/시점, 사업/재무 유형, 선택한 재무항목의 메커니즘,
회사 사업 anchor와 조건·모니터링 방향을 추가 검사한다. 인용은 검증된 locator로
복원한다. source ID가 맞는 것만으로 주장을 정당화하지 않는다. 자동 검사로 보장할
수 없는 경제적 설득력과 독립 의미 정확도는 NOT_MEASURED다. 역할별 부분 수락만
메모에 반영하며 나머지는 기존 결정론적 문구를 유지한다. 실제 모델 수락0이면
AI_FALLBACK이다. 기본 실행/ETF/dry-run은 모델을 호출하지 않는다.

기존 P5 코드18개를 v5/에 바이트 그대로 보존해 과거 결과를 원래 방법으로 재생한다.
소프트웨어 회귀 성공과 실제 모델 품질 게이트는 별개다. 로컬 입력에서도 시장가격·
시총·배수·peer·시나리오 지분가치·뉴스·private 경로·API credential 전송은 계속 금지한다.

## 기존 P5 방법

P5는 기존 P4 계산을 그대로 사용해 한국어 `MEMO.ko.md`를 추가한다. Evidence Pack은
SEC 사실·계산·연구 가정·출처 충돌·결측을 구분하며, 시나리오 검토는 원래 입력이나
산출액을 수정하지 않는다. 현재 공시의 연간 비교값은 당시 PIT나 장기 평균이 아니다.
MU의 최근 마진 절반은 연구 가정이며53주 기간을52주와 평균내지 않는다.

```powershell
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT --memo --offline
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT --memo --ai --offline
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MU --ai-dry-run --offline
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MU --memo --scenario-horizon 3
```

기본 실행은 모델·네트워크 호출0이다. `--ai`만 기존 pinned Qwen3-4B GGUF와
CUDA llama.cpp B 실행기를 사용한다. 새 모델 다운로드·유료/외부 API 경로는 없다.
model revision/파일 SHA/runtime SHA·실제 GPU identity·원시 응답·토큰 사용량을
private receipt에 저장한다. 기존 GPU 정책의 host RAM2GB/여유 VRAM5GB 기준과
공유 lock을 준수하며 CPU 자동 대체나 다른 작업 종료를 하지 않는다.
메모당 THESIS/COUNTER_THESIS/MONITORING 최대3회, 요청당 출력 상한960토큰,
문맥4096토큰, 요청180초/시작120초의 기존 제한을 사용한다. USD 유료 비용은0이며
로컬 전력·시간은 별도다. 실패/차단 시 원래 결정론적 메모를 유지한다.

모델에는 target issuer의 공개 SEC 근거만 화이트리스트로 전달한다. 시장가격·시총·배수·
peer 수치·시나리오 지분가치·exit multiple·뉴스·전체 private 보고서·인증정보는 제외한다.
라이선스가 불명확한 데이터는 로컬 모델에도 보내지 않는다. dry-run은 전송 범위만
요약하며 모델을 실행하지 않는다. 원문 속 지시문은 격리한다.

AI는 숫자를 생성하지 않고 Evidence ID·정확한 원문 anchor와 해석을 제안한다.
인용 숫자는 코드가 원래 계산에서 표시한다. 출처/역할/수치/금지 주장 검사를 통과한
실제 모델 응답만 AI_ASSISTED로 표시한다. Mock은 실제 추론으로 표시하지 않는다.
검사 범위를 통과해도 경제적 설득력·독립 의미 정확도는 NOT_MEASURED다.
미측정 영역은 claim_validation/memo_limitations에 남기며 기존 실패한 ResearchEngine
품질 관문·SHADOW·23요인·null 점수/목표가 정책을 바꾸지 않는다.

`--validate`는 저장된 응답으로 동일 파이프라인을 재생한다. 모델을 다시 호출하지 않는다.
기존 v1/v2/v3/v4는 byte-exact archive로 재생한다. 결과는 private 전용이며 외부 공개나
commit/push를 자동 수행하지 않는다.

## 기존 P4 Conditional Valuation

P4는 기존23요인 점수와 분리된 실험적 비교·조건부 시나리오다. ticker-only 기본
사용법은 유지하며 실제 재무 입력에 명시적 미래 성장률·마진·종료 배수를 적용한다.
산출액은 미래 시점의 조건부 지분가치이며 현재 적정주가·수익률·매매 신호가 아니다.
EV의 미래 순차입금이나 적절한 미래 주식 수를 만들지 않는다. 실제 주당 가치는 보류한다.

```powershell
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT --offline
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MU --scenario-horizon 3
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT --scenario-assumptions assumptions.json
```

사용자 JSON은 `{"base":{"growth":"0.1","net_margin":"0.2","exit_pe":"15"}}`처럼
bull/base/bear별 필드를 지정한다. 이는 형식 설명용 ILLUSTRATIVE 예시다.
지원 필드: growth, net_margin, operating_margin, fcf_margin, exit_pe, exit_ps.
ratio는0.1=10%이며 horizon은1/3년, 성장 복리 적용이다. 사용자 입력은 USER_ASSUMPTION,
기본 미래 값은 ILLUSTRATIVE_ASSUMPTION이다. 실제 기업 가이던스나 컨센서스가 아니다.
MU 기본 순이익률은 관측치의50%를 쓰는 연구용 정상화 가정이며 역사 평균이 아니다.
53주 실적은 원래371일 규모를 유지하고 정확한 달력12개월로 보정하지 않는다.

비교 후보는 MSFT ORCL/AMZN/GOOGL, MU SNDK/NVDA이며, 같은 산업만으로 승인하지
않는다. 자기 공시의 제품 근거·재무·시장 기준을 확인하고 직접/부분/참고/제외를 구분한다.
중앙값과 할증/할인은 지표별 최소2개 서로 다른 issuer가 있어야 산출한다. FCF Yield는
%p 차이다. 단일 peer는 개별 관측으로만 표시하며 산업 평균으로 부르지 않는다.
ORCL 총매출과 AMZN PP&E cash purchases는 원문 표시 행에 제한해 기존 bridge/calculator로
연결하며, 상충 값을 덮지 않는다. 원래 coverage.py/valuation.py는 그대로다.

기본은 오프라인이다. 명시적 --fetch-missing은 기존 target 수집과 함께 MSFT의 ORCL/AMZN,
MU의 SNDK만 제한 취득한다(각 시장3회/SEC8회 상한, 기존 공유 예산·간격·실패 정책).
BE/XOM은 새 peer를 자동 취득하지 않는다. 새로운 크롤러·유료 API·모델 호출은 없다.
역사적 자료는 실제 가격 범위와 결측 사유를 표시하며 PIT 자료가 없으면 percentile을
만들지 않는다. 3×3 민감도는 기본 시나리오에서 마진×P/E와 성장률×P/S만 변경한다.

이전 personal-research-v3 소스10개는 `v3/`에 byte-exact로 보존했다. v1/v2도 유지한다.
SHADOW·기존 score/price_target null·23요인·예약 작업·locked final은 변경하지 않는다.

## 보존된 P2.1 / P3 기능

P2.1은 원문 표시 행에 근거한 보통주 자본·총매출·한정된 D&A 구성을 연결한다.
BE의 total revenue와 customer-contract revenue를 합산하지 않으며, 보통주 귀속이
명시된 자본 행을 P/B에 사용한다. 최근 annual 실적은 LATEST_FISCAL_YEAR로
표시하고 정확한12개월 TTM과 구분한다(MU의 53주 공시 포함). EV는 finance lease를
포함하고 operating lease는 제외하는 정책이지만, 중복 없는 완전한 차입금과
preferred/NCI 조정이 확인되지 않으면 보류한다. 총부채는 차입금이 아니다.

P3은 저장된 SEC Item1/1A/7 및 10-Q Item2 문단과 사업부 XBRL을 읽는다.
`business.py`는 모델 없이 제한된 원문 문단을 분류하고 정확한 HTML character span을
보존한다. SOURCE_FACT는 회사의 공시 내용이며 독립적으로 검증된 경쟁 우위가 아니다.
ANALYST_INTERPRETATION은 해당 사업 사실과 실제 재무/사업부 숫자에 묶는다.
산업 성장률·시장점유율·뉴스 본문·미래 수치를 창작하지 않는다. 정성적 시나리오는
SCENARIO_ASSUMPTION이며 확률·목표가·매매 신호가 아니다.

사업부는 business-segment axis만 허용하고 지역·제품 차원을 섞지 않는다.
같은 공시의 동일 member 및 같은 기간을 대조하며, 합계 불일치를 자동 보정하지 않는다.
사업부 영업손익과 연결 손익은 다를 수 있고 재편 전후 명칭을 연결하지 않는다.
ETF는 기업용 사업·segment·배수를 적용하지 않는다. 소스 부족은 부분 보고서로 남긴다.

기존 P2 소스7개는 `v2/`에 byte-exact로 보존해 기존 P2 보고서를 재생한다.
기존 `v1/`과 그 validator도 유지한다. 신규 코드는 개인용 경로 안에 한정한다.
실제 원문 연결/산술 검사와 독립적인 사업 의미 정확도·투자 성과 검증은 다르다.

## 보존된 P2 기능과 사용법

기본 ticker-only 인터페이스와 오프라인 동작을 유지하면서 실제 시장 종가·ETF 분기·
Decimal밸류에이션을 추가했다. 기존 v1소스는 `v1/`에 byte-exact로 보존했고
`--validate`는 해당 버전의 원래 validator로 과거 보고서를 재생한다.

```powershell
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MU --fetch-missing
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker SPY --offline
```

`--fetch-missing`은 없거나 오래된 가격을 기존 Massive 무료 EOD transport로
취득한다(종목별최대3요청, 공유15초간격, timeout20초, 재시도0).
SEC재무자료가 없으면 기존 SECcollector를 별도 `personal_sec/real`저장소로
실행한다(최대8요청). 기존 계정 이용조건·환경설정이 충족돼야 하며 키는 출력하지 않는다.
`--offline`과 `--fetch-missing`은 동시 지정할 수 없다. 기본 실행은 네트워크0이다.

원본 가격/자료는 `reports/private/personal_market/real`에 저장한다. 이전
`market_inputs`의 봉인된 자료도 읽을 수 있으나 조정 가격을 실제 종가로 이름만
바꾸지 않는다. raw_close와 split-adjusted close, 배당조정 여부·FIGI·거래소·
실제 거래일·장종료·취득·등록시각을 분리한다. 주말/휴장/장중은 기존 거래소
캘린더로 최근 완료 세션을 선택한다. 오래된 자료는 STALE_DATA다.

새로 받은 과거 bar는 실제 취득 이전 분석에 소급 사용하지 않는다. 원래 시점의
관측 vintage가 검증됐다는 주장도 하지 않는다. 가격수집 실패와 SEC실패는 별도다.
금융자료가 없어도 가격자료가 있으면 price-only/PARTIAL보고서를 생성한다.
ETF는 Market Snapshot만 제공하고 기업용 PER/EV/EBITDA를 적용하지 않는다.

TTM은 정확한 `연간 + 당년YTD − 전년동기YTD`의 연속 구간을 증명할 때
산출한다. 후속 분기 공시가 아직 없으면 최근 연간 공시의 실제 회계기간을
LATEST_FISCAL_YEAR로 사용하고 52/53주 및 실제 일수를 구분한다. 분기×4, YTD중복, TTM가중평균EPS
단순합은 허용하지 않는다.
P/E는 정렬된 제공처 시가총액/TTM지배기업이익 경로를 우선하고, 주당EPS경로는
정확한 증권·통화·분할기준이 있을 때만 허용한다. P/S는 명확한 매출 범위,
P/B는 우선주를 제외한 지배기업 보통주자본, FCF Yield는 TTM현금흐름을 요구한다.
EV는 총부채가 아닌 완전한 이자부 차입금·현금·기타 조정범위가 필요하다.
EBITDA는 지원되는 단순 영업손익+D&A 정의다. 없는 입력을0으로 채우지 않는다.

## 유지한 개인용 재무 경로

저장된 SEC 자료에서 티커별 개인용 재무·성장·위험 보고서를 생성한다.
사람의 사전 승인, owner trust, 수동 receipt를 입력받지 않는다. 기존 strict
EvidenceAdapter/ImpactEngine와 별개의 읽기 전용 소비 경로이며 자동 원문 관측을
strict VERIFIED로 바꾸지 않는다. P1.5 계약 취득은 선행 조건이 아니다.

프로젝트 `Stocker` 폴더에서:

```powershell
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker MSFT
.\.venv\Scripts\python.exe -m extensions.personal_research --ticker BE --as-of 2026-11-12
.\.venv\Scripts\python.exe -m extensions.personal_research --validate <생성된 폴더>
```

기본은 네트워크/모델 호출0인 로컬 실행이다. `--as-of`의 날짜는 한국 시간 해당일
종료다. 날짜를 소급해도 실제 receipt/source 최초 이용시각 이후인 자료만 읽는다.
공유 latest/state를 수정하지 않고 authoritative immutable snapshot을 읽는다.
기본 출력은 `reports/private/personal_research/<UUID>/`의 RESULT.json, INPUT.json,
REPORT.ko.md, FILES.json이다. 일부 자료가 부족하면 PARTIAL, 핵심 자료/종목이
없으면 UNAVAILABLE 보고서를 만든다. SEC자료가 없는 종목의 재무 숫자를 만들지
않으며, 별도로 확보한 시장자료가 있으면 가격 중심 보고서를 제공한다.

기존 `sec_filing_evidence.parse`, `financial_fill.select`의 단위·기간·행·차원 대조,
`weighted_features.financial_factors`의 재무 산식과 신선도/범위 검사,
`sec_growth.change`의 전년 음수/0 처리를 재사용한다. net income의 연결 전체와
지배기업 귀속을 구분한다. 연간·분기·YTD·기말 잔액을 함께 더하지 않는다.
기존 행 이름의 철자만 미지원인 경우 정확히 하나인 표준 태그를 기존 eligible 검사로
대조한다. 원문이 없으면 기존 companyfacts normalize를 재사용하되 정확한 JSON 필드
경로를 기록한다. 원문 충돌을 API값으로 덮거나 XOM 매출의 차원 범위를 생략하지 않는다.

첫 릴리스는 공시9개 핵심 항목, 동일 기간의 영업이익률·FCF·현금/총부채·총부채/자산,
같은 공시의 정확한 전년 동기 비교를 지원한다. 각 단위·귀속 범위와 자료가 일치할
때만 산출한다. source_refs는 원문 SHA/URL/DOM/context/row를 추적한다.

뉴스 귀속·DCF·정밀 계약·ROE/ROIC/순차입금 등은 연결되지 않았다.
저장 자료가 존재할 수도 있으므로 이를 자료 자체가 없다는 뜻으로 해석하지 않는다.
정성 시나리오는 ESTIMATED 가정이며 숫자 예측이나 발생확률이 아니다.
23요인 모델과 SHADOW/실패한 ResearchEngine 평가를 유지하고 새 점수·목표가·매매
추천을 산출하지 않는다. 기본 보고서는 이 선택적 단계의 부족으로 실패하지 않는다.
