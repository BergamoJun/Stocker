# Equity Research Lab

[RAM 확보 후 실제 Qwen 재검증](reports/private/critic_resume/20261110/851d20a11d064565b91184fbd55f4bee/SUMMARY.ko.md)을 완료했다. 기존 36개 입력·정답·프롬프트·기준을 유지했고 실제 모델 호출 36회, 기존 회귀 5/12, 새 모델 눈가림 합성 2/24, 잘못된 원시 승인 1건으로 quality_gate_failed 판정이다. 기존 미실행·실패 보고서는 당시 기록으로 보존한다. 독립 실제 사건·가격 예측력은 여전히 미검증이며 투자 점수·목표가는 보류한다.

[AI 신뢰성·기업 노출·사건 평가](reports/private/reliability_case/20261110/DEVELOPMENT_REPORT.ko.md)를 1→2→3 순서로 개발하고 각 검사·검토 이력을 남겼다. 신규 104개, 전체 1,223개 통과·1개 환경 건너뜀. 새 AI 후보는 메모리 부족으로 미실행이며, 기업별 직접 경제 노출과 실제 사건/가격 성능은 미확정이다. 기존 실패·23변수·가격 계수·최종 잠금·예약 5개와 추천/목표가 보류를 유지한다.

[기업별 조건부 영향·반론 연결](extensions/ISSUER_IMPACT.md)을 별도로 구현했다. BE/MSFT/XOM의 SEC 숫자·허용 공식 문단·기존 뉴스 링크를 연결하고 실제 보고서 3건을 재생했다. 실제 로컬 모델 품질 시험이 고정 기준에 미달해 기업 보고서의 AI 채택은 보류한다. 주가 효과·추천 점수는 보류한다. 새 검사 41개, 전체 1,119개 통과·1개 환경 건너뜀. [결과와 재개 조건](reports/private/impact_case/20261109/SUMMARY.ko.md).

[요청형 최근 N일 뉴스](extensions/ON_DEMAND_NEWS.md)를 추가했다. BE/MSFT/XOM 입력 후 1~30일(기본 7일)의 링크를 검색하고 원래 시각을 보존한 15분 재사용·미검색 구간 표시·용량 제한을 적용한다. 상시 뉴스 예약은 추가하지 않는다. 실제 BE 7건·MSFT 25건(상한)·XOM 2건, BE 재조회 HTTP 0회를 확인했다. GDELT 429 범위는 대기로 남기며 본문·외부 AI·뉴스 점수는 활성화하지 않는다. 전체 1,078개 통과·1개 환경 건너뜀. [결과·실행 방법](reports/private/on_demand_case/20261109/SUMMARY.ko.md).

[거래대금 근사·지속성 후보](extensions/TURNOVER_RESEARCH.md)를 별도 구현했다. 직전 20일 대비 종가×거래량과 3·5일 지속성, 가격·시장/업종 반응을 계산하며 실제 세 종목 보고서를 재생했다. 원래 거래량 계수 2를 같은 비중에서 교체하는 비교이고 기관 매수·뉴스 선행 매수·높은 신뢰도를 인증하지 않는다. 전체 1,046개 검사를 통과했고 기존 과거 평가와 가격 9변수 원본 재생 완료 뒤 자동 비교하는 다섯 번째 예약을 등록했다. [결과·현재 상태](reports/private/turnover_case/20261109/SUMMARY.ko.md).

[로컬 AI 원문 판독·자동 후속 평가](extensions/AUTONOMOUS_RESEARCH.md)를 추가했다. 허용된 연준·EIA 원문을 프로젝트 내부 Qwen3-4B로 분류하고 BE/MSFT/XOM 보고서에 원문 링크·조건부 경로·반론을 저장했다. 제한적 합성 준수 시험 16건과 전체 코드 검사 1,012개를 통과했다. 기존 과거 작업 전체가 끝나면 결과 해석과 가격 변수 9개 계수 관련성 검사를 5분 간격 예약으로 이어간다. 이는 주가 예측 성능·전체 뉴스 40점 축·정식 추천도 승인과 별개다. [재개 검사·실제 결과·상태](reports/private/autonomous_case/20261109/SUMMARY.ko.md).

[자동 가중 분석 엔진 v1](extensions/WEIGHTED_ENGINE.md)을 추가했다. 수익 기회 25·가격 15·기업 실적 10·시장/업종 10·최신 사건 40을 23개 변수로 세분화하고 기존 5거래일 통계 모형·로컬 사업 경로 규칙·반론/위험 검사를 연결했다. BE/MSFT/XOM 연구 보고서 3건을 자동 생성·재생했고 신규 58개를 포함해 전체 969개 검사를 통과했다. 뉴스 5개 축과 XOM 일부 재무의 부족은 가중치 재분배 없이 보류한다. 외부 AI의 범용 기사 추론·계수 실증 검증·정식 추천도 제공은 후속 단계다. [쉬운 결과 설명과 보고서](reports/private/weighted_engine_case/20261109/SUMMARY.ko.md).

2026-11-09 독립 작업 3→4→5 완료: [공통 분석 계약](extensions/ANALYSIS_CONTRACT.md), [다종목 종가·시장·업종 입력](extensions/MARKET_INPUTS.md), [기업별 재무 누락 보완](extensions/FINANCIAL_FILL.md)을 별도 구현했다. BE/MSFT/XOM과 ETF 4개에 각 480개 일봉을 연결했고, XOM 영업매출을 원문 분류·합계 대조로 보완했다. 재무는 BE 18/18·MSFT 18/18·XOM 8/9이며 XOM 영업손익·동일 CIK 연간 이력 공백은 보류한다. 전체 검사 911개, 이전 보고서 24건·공통 입력 6건·공식 문서 4건·예약 3건과 새 최종 입력 3건의 재생을 확인했다. AI 입력과 로컬 가격은 분리하며 자동 해석 엔진·정식 목표가·매력도 제공은 다음 단계다. [개발 결과와 최종 보고서](reports/private/current_inputs_case/20261109/SUMMARY.ko.md).

제품 목표는 **종목 입력 → 최신 확보 자료 자동 수집 → 재무·가격·시장/업종·뉴스·위험 자동 분석 → 투자 매력도와 근거 보고서 작성**이다. 사용자가 기사마다 판단을 입력하는 절차를 정상 이용의 필수 조건으로 두지 않는다. 현재는 수집·계산·보고서 기반과 로컬 고정 규칙의 자동 가중 분석·반론 검사가 구현됐으며 범용 AI 사업/뉴스 추론은 후속 단계다. 다음 개발 우선순위를 [자동 분석 계층과 종합 평가 연결](docs/ARCHITECTURE.md#2026-11-09-제품-목적과-다음-구현-우선순위)로 정정했다. 사람 검토는 선택적 교정과 품질 감사에 사용한다.

[공식 원문 입력](extensions/README.md)을 별도 구현해 연준·EIA 자체 작성 자료 4건·22문단의 실제 저장과 문단별 인용 입력을 확인했다. 일반 언론사 본문이나 가격의 외부 AI 사용권을 확대하지 않는다. 현재 회사 메타데이터 수집기는 [공유 요청 큐](extensions/API_QUEUE.md)의 `extensions.queued_news`이며 같은 계정 잠금·간격·429 대기를 사용한다. 전체 검사 813개를 통과했다. [두 단계의 실행 결과](reports/private/source_access_case/20261109/SUMMARY.ko.md)에 실제 수집 범위와 남은 조건을 기록한다. 자동 분석 엔진·외부 모델 호출은 아직 연결하지 않았다.

[기업별 뉴스 갱신·검토 연결 확장](docs/CONTEXT_EXPANSION.md)은 기존 계산과 보고서를 보존하고 BE·MSFT·XOM의 검색·저장·원문 검토를 분리한다. 원래 전송 버전 `context_adapters.news_refresh_v2`와 보고서 `research.expanded_report`를 보존한다. 작업 전후 전체 변경 범위·예약·이전 보고서를 검사하며 가격·추천 검증과 원문 영향 검토의 부족은 별도로 표시한다.

원문 영향 검토는 현재 검토자가 기사 사실·사업 영향·반론을 입력하고 프로그램이 출처·시각·충돌을 검사하는 방식이다. 자동 AI 판독과 뉴스 사건별 사후 가격 평가기는 아직 없다. 당시의 판단과 이후 가격 반응을 나누는 기준은 [검토 방식 설명](docs/CONTEXT_EXPANSION.md#원문-영향-검토와-발표-후-주가-평가는-서로-다른-기록이다)에 있다. 11월 9일 후속 조회에서는 거시정치 링크 25건과 공식 보완 3건을 새 부분 보고서에 연결했으며, 기업 영향 검토는 대기다.

[재무·가격·뉴스 종합 보고서](docs/INTEGRATED_REPORT.md)를 추가했다. `python -m research.integrated_report --ciks 1664703 789019 2115436`으로 원문 검토 재무와 기간 비교, 기업별로 확인한 가격 시나리오·ETF·기사 버전 검토를 같은 기준시각에 연결한다. 저장 자료로 개인용 보고서를 생성하며 입력 사본·최초 연결 시각·파일 묶음 재생을 지원한다. BE 이외의 가격·뉴스 연결은 미지원 사유를 표시하고 정식 목표가·매력도는 검증 대기로 유지한다.

[전분기·최근 12개월 분석](docs/SEC_QOQ_TTM.md)을 별도 구현했다. `sec_chain_sources`로 이전 기간 원문을 보완하고, `sec_chain_review`의 공시 간 검토를 거쳐 `sec_chain_report`에서 직접 분기·누적 차감 분기·최근 12개월을 구분한다. 수정본·기간·귀속 범위·검토 변경을 확인하며 기존 보고서·가격/뉴스 모델·예약 작업은 유지한다.

[연간·전년 대비 분석](docs/SEC_GROWTH.md)을 별도 모듈로 추가했다. `research.sec_period_review`로 같은 공시의 전년 비교 열을 검토하고 `research.sec_growth_report --ciks ...`로 증감률·증감액·손익 전환·이익률 %p를 보고한다. 적자·0 기준 성장률은 보류하며, QoQ/TTM은 위의 별도 기간 연결 계층에서 제공한다. 기존 검토·가격 모델은 유지한다.

[공시 원문 검토·다종목 재무 보고서 연결](docs/SEC_FACT_REVIEW.md)을 추가했다. `research.sec_fact_review`로 원문 행·기간·범위·선택 이유를 기록하고, `research.sec_financial_report --ciks ...`로 검토 수치와 보류 사유를 가진 보고서를 만든다. 원문 변경·수정 공시·검토 충돌·과거 시점을 확인하며 가격 계획·매력도는 보류한다.

다종목 [SEC 공시 자동 갱신](docs/SEC_AUTO_REFRESH.md)을 추가했다. 관심 목록을 CIK(SEC 기업 식별번호)로 묶어 공시·표준 재무·원문을 보관하고 수정과 최초 이용 가능 시점을 남긴다. `python -m research.sec_refresh`로 실행하며 기본 연결 확인 목록은 BE/MSFT/XOM이다. 자동 재무는 원문 대조 전 자료이며 기존 BE 검토 보고서·가격 예측·과거 평가와 구분한다.

종목/섹터를 입력하면 **출처가 추적 가능하고 재현 가능한 투자 리서치**를 바탕으로, **약 5거래일 스윙매매에 참고할 조건부 가격 시나리오**를 먼저 보여주는 서비스를 개발하는 프로젝트.

> 현재 범위 (2026-11-07 사용자 변경): **개인용 로컬 리서치, 월 데이터 예산 $0**. 공개 웹서비스 설계는 후속 검토로 보류한다. BE 개인 가격 연결과 480개 일봉 확보, 가격 사전 진단·향후 예측 기록·다음 제공 시각의 1회 재실행을 구현했다. SEC 자동 수집·원문 검토·다종목 재무 보고서는 별도 모듈로 구현했고 실제 모델 성능 검증·LLM API는 미완료다.

## 현재 우선 작업: 가격·모델·최신 뉴스 준비

2026-11-08 우선순위 변경: **산업군당 서로 다른 기업 최소 100개의 과거 성능 평가를 모델 개발의 주 경로로 삼고**, BE 5거래일 예약 관찰은 보조로 유지한다. 별도 일반 종목 가격 어댑터, 공개된 FF12 SIC 범위에 따른 12개 산업군 분류, 성과를 보기 전 기업 선정, 중단 후 재개 가능한 무료 API 수집·고정 수식 평가기를 구현했다. 실제 BE 자료의 66개 기준일로 실행 경로를 확인했으며, 여러 산업군의 성능 평가는 수집 완료 이후다. [설계와 한계](docs/HISTORICAL_EVALUATION.md), [실행·상태 확인](docs/HISTORICAL_PANEL_RUNBOOK.md).

`python -m research.historical_preflight`는 계속 준비 계산만 한다. 실제 작업은 `research.panel_job`이며 최대 1,200개 기업을 목표로 한다. 산업군별 20% 기업과 마지막 기간의 정답은 잠근다. 100개 미만인 산업군과 가격 누락은 부족으로 남긴다. 현재 자료는 당시 원본 상태를 보증한 이력이 아니며, 학습·튜닝·추천 점수 공개는 수행하지 않는다.

`python -m research.panel_diagnostics --job <job.json>`으로 저장 결과의 정확도·비용 후 비교 성적·기간 묶음의 불확실성·자료 부족을 해석한다. 수집·평가 완료 전에는 대기 보고서를 만들며 기존 작업은 변경하지 않는다. `--demo`는 가상 사례, `--pilot <BE 실행 폴더>`는 기존 66개 기준일의 읽기 확인이다. [구현·추정 조건·실행 방법](docs/PANEL_DIAGNOSTICS.md).

`python -m research.news_connections`는 보관된 기사 버전과 검토를 연결하고 주요 사건·원문 링크·미검토/재검토 사유를 보여준다. `--capture <근거파일>`은 보관과 빈 검토 양식, `--import-review <검토파일> --context <근거파일>`은 변경 불가 검토 수신 기록, `--forecast <예측파일>`은 원본을 바꾸지 않는 시점별 연결 문서다. 새 API·본문 수집·자동 AI 판독·추천 점수는 추가하지 않았다. [기사 연결의 조건과 저장 방식](docs/NEWS_CONNECTIONS.md).

첫 실제 관찰의 자동 작업을 Windows 작업 스케줄러에 등록했다. **2026-11-14 14:02 한국 시간**에 기존 BE 예측을 조회·평가하며, 제공 지연 시 15분 간격으로 제한 재시도 후 완료 시 자동 중지한다. 현재 사용자 로그인 상태가 필요하며 예약을 놓치면 다음 사용 가능 시점에 실행한다. [예약 상태·결과 위치·동작 조건](docs/BACKGROUND_OBSERVATION.md).

관찰을 기다리는 동안 `python -m research.news_archive`로 뉴스 메타데이터의 실제 보관 시각과 내용 버전을 축적한다. `--as-of <시각>` 조회에는 그때까지 보관한 내용만 포함한다. 과거로 시각을 소급하거나 보관만으로 ML 학습권을 부여하지 않는다. [시점별 뉴스 보관 방법](docs/NEWS_ARCHIVE.md).

월 데이터 예산은 **$0**이다. 추가 투자 보고서 생성에 앞서 가격 사용권, 목표·매력도·기간 모델의 실제 데이터 검증을 진행한다. **Massive Stocks Basic**을 개인용 가격 공급자로 선정했다. 무료 EOD·2년 이력·분당 5회이며 개인 저장/분석을 허용한다. 본인 무료 계정의 비전문가 자격·구독자 조건 확인과 API 키가 필요하다. 공개 배포와 가격의 외부 LLM 전달은 OFF다.

```bash
python -m research.fetch_prices --check
python -m research.fetch_prices --confirm-personal-nonprofessional
```

둘째 명령은 본인의 자격·필수 동의가 완료된 경우에만 실행한다. `MASSIVE_API_KEY`는 로컬 프로세스 환경변수에 설정한다. `.env` 자동 로드는 하지 않는다. 키를 채팅에 보내지 않는다. 설정과 다음 계산 명령은 [가격 연결 안내](docs/MARKET_PREPARATION.md#8-개인용-무료-가격-연결)에 있다.

ATR14 기준 목표/무효화 후보, XNYS 5거래일 계산, 비용·갭·동일 일봉 양방향 도달을 처리하는 검증 도구와 매력도 후보를 구현했다. **후보 모델이며 실증 검증은 완료되지 않았다.** 최신 사건 비중은 실험용으로 40/100으로 두고, 최신 악재도 크게 감점한다. 점수는 검증 전 공개하지 않는다.

무료 GDELT 메타데이터 API는 기업·섹터·거시정치·원자재 뉴스 링크를 수집하고 별도 하이라이트 문서를 만든다. 원문 본문/이미지는 가져오지 않는다. 관측시각과 원 발행시각을 구분하고, 미검토 뉴스에는 점수를 부여하지 않는다. API 장애·지연과 제한된 검색 범위를 표시한다. 상세: [가격·모델·뉴스 준비 명세](docs/MARKET_PREPARATION.md).

```bash
python -m research.prepare_models
python -m research.prepare_models --collect-news
```

첫 명령은 준비 상태만, 둘째는 네트워크 조회 후 준비 상태와 `news_metadata.json`·`news_highlights.md`를 `reports/private/model_preparation/`에 저장한다. 상시 실시간 서비스나 검증된 추천 보고서가 아니다.

다음 개발 단계를 실행하려면 `python -m research.advance`를 사용한다. 무료 제공 시각까지 기다린 뒤 한 번 더 실행하려면 `--defer-until-price`를 붙인다(최대 4시간, 반복 서비스 아님). 현재 조회한 과거 가격의 사전 진단과 실제 진입 전 예측 기록을 구분하며 추천 수치는 검증 전 보류한다. [고정 평가 규칙과 실행 상태](docs/VALIDATION_PROTOCOL.md).

## 고점·수익 기회 알고리즘 1차 구현

뉴스 후속 경로는 `python -m research.official_news`로 무료 연준·EIA의 공식 RSS 제목/날짜/원문 링크를 보완하고, `python -m research.event_review`로 원문 검토 대기 목록과 입력 양식·후보 뉴스 점수를 만든다. 원문 발행 시각·BE와의 관련성·호재/악재·반론·확인할 사건을 기록해야 하며, 미검토 기사나 미확인 주제가 남으면 점수를 계산하지 않는다. [뉴스 검토 방법과 해석](docs/NEWS_EVENT_REVIEW.md).

`python -m research.context_report`는 저장된 BE 가격·공시·시장/청정에너지 ETF·뉴스 링크를 하나의 개인용 보고서로 연결하고, 다음 거래 시작 전 판단을 덮어쓰지 않는 기록으로 남긴다. 각 항목이 무엇을 평가하고 왜 보류됐는지 설명한다. 새 무료 자료 조회는 `python -m research.fetch_context`(ETF 2건), `python -m research.massive_news`(BE 뉴스 메타데이터 1건)로 분리한다. 뉴스 링크는 영향 검토 전 점수에 가산하지 않는다. [연결 방법·해석·실제 검증 일정](docs/EVIDENCE_CONTEXT.md).

`python -m research.trade_readiness`는 저장된 최신 BE 가격의 개인 권한·현재성을 확인해 고정 통계 후보에 비용·위험 필터를 적용한다. `python -m research.trade_evaluation`은 새 시드 24개의 같은 ML 예측을 필터 전후 비교한다. 표본 검토의 불확실성을 유지한 별도 승인 연구 단계이며 실제 ML 학습이나 추천 승인 경로가 아니다. [비용·위험·보류 조건](docs/TRADE_SCREEN.md).

`python -m research.sample_audit`는 독립 경로 24개로 표본을 늘리고 학습량을 고정한 채 평가 43→86개를 비교한다. 사전 정의한 전체 불확실성 구간이 연구 허용오차 안에 있을 때만 다음 연구 단계 진행 조건을 통과한다. [표본 재검토 정책](docs/SAMPLE_AUDIT.md). 합성 표본 확대는 실제 BE 검증을 대체하지 않는다.

`python -m research.robustness`는 같은 잡음·새 시드 3개·거래량 포함/제외·추세 반전/변동성 급등의 후속 합성 비교를 실행한다. 15개 데이터셋·30개 학습 구성, 공통 사례 비교와 비용 후 손익 경계를 보존한다. [후속 검증 정책](docs/ROBUSTNESS_PROTOCOL.md), [무료 학습 데이터 추가 검토](docs/TRAINING_DATA_REVIEW_20261107.md). 실제 BE 성능 검증이나 엔진 교체 승인을 뜻하지 않는다.

`python -m research.peak_research`는 고정 EWMA 통계·갭/장중 경로 시뮬레이션으로 5세션 고점 구간·형성일, 목표/무효화 후보, 목표 우선 도달기간, 비용 후 수익 범위와 수익 기회 가산점을 계산한다. BE 실제 가격 진단과 합성 반례 테스트를 수행했다. 미검증 연구용이며 전체 추천 점수/보고서 목표는 계속 보류다. [수식·파라미터·검증 범위](docs/PEAK_MODEL.md).

`python -m research.costs`는 데이터 요청량과 향후 LLM 토큰을 별도로 산출한다. 현재 알고리즘의 LLM 토큰은 0이며, 유료 플랜/LLM 호출은 활성화하지 않았다. GDELT는 공유 직렬 호출·최소 15초 간격·429 대기로 제어한다. [무료량·토큰·요금 비교](docs/API_COST_ESTIMATE.md).

## 블룸에너지 SEC 보고서 실행

프로젝트 폴더에서 개발 의존성을 설치한 뒤 실행한다. 아래 CLI는 네트워크/API 키 없이 저장된 실제 공시 수치와 검토 문안으로 JSON·한국어 Markdown·계산 추적 파일을 만든다.

```bash
python -m pip install -e '.[dev]'
python -m research --snapshot research_inputs/BE/2026-09-30.json --ticker BE
```

결과는 `reports/private/<report_id>/`에 저장하며 기존 보고서를 덮어쓰지 않는다. 입력은 **실제 과거 SEC 공시의 선택 수치**이며 실시간 가격이 아니다. 가격·목표가·매력도는 보류한다. 생성 CLI가 AI 분석이나 SEC 실시간 수집을 수행한다고 해석하면 안 된다. 범위·산식·검증 기준은 [SEC 파일럿 명세](docs/SEC_REPORT_PILOT.md)에 있다.

## 현재 확정 조건 (2026-11-07)

- **개인용 로컬 CLI**, 월 데이터 예산 $0; 공개 웹은 보류
- **미국 상장 주식과 ETF** 우선
- **종가(EOD)·지연 시세** 중심. 개인 수집·저장·결정론적 계산은 해당 개인용 권리로 진행하며 공개/외부 AI 사용권과 분리
- 기존 기업·섹터 리서치를 근거로 유지하고, 기본 분석 기간은 `swing`(약 5거래일)으로 설정
- 리포트 상단에 기준가격·1차 목표가·수익실현 검토 조건/시기·무효화 기준·손익비·매력도(1~100점)·한줄평 배치
- 가격 시나리오와 매력도는 별도 제공 조건을 적용. 검증된 모델이 없는 점수와 도달 예상기간은 산출 보류
- SEC 공시 기반 기업·재무 리서치를 첫 번째 end-to-end 범위로 구현하고, 스윙 기능용 가격 데이터 사용권 확보를 병행
- 현재 결과는 무시 목록의 개인 폴더에 저장. 익명 조회·작업 큐·공개 예산 통제는 향후 웹 단계에서 적용
- FRED API의 AI 관련 이용 제한 때문에 직접 연결은 보류하고 BLS·BEA 등 원천 기관 사용을 검토
- 구체 명세: docs/MVP_SPEC.md, docs/PUBLIC_ARCHITECTURE.md, docs/API_CONTRACT.md, docs/SOURCE_LICENSING.md, docs/OPERATIONS.md

## 제품 목표

1. 미국 개별주식, 일반 ETF, 레버리지/인버스 ETF, 산업 섹터를 **서로 다른 분석 방법론**으로 다룬다.
2. 시장/재무 데이터와 뉴스·공시를 수집하고 **출처, 공표일, 조회시각, 기준시각**을 보존한다.
3. 검증된 수치 계산을 거친 후 AI가 강세·중립·약세 논리와 반례를 정리한다.
4. 판단의 근거, 반증 조건, 불확실성, 데이터 공백을 보고서에서 명확히 노출한다.
5. 기준가격에서의 상승여력과 하방 위험, 관찰기간, 재평가 조건을 스윙 요약으로 먼저 전달한다. 매력도는 승률과 구분한다.
6. 사용자의 실제 포지션을 고려하는 기능은 별도 권한/규제 검토 후 확장한다.

## 스윙 리포트 구성과 현재 상태

기본 화면은 **스윙 한줄평/가격 시나리오 → 기업·섹터 근거와 반론 → 지표·출처·한계** 순서다. `swing`은 기준가격 세션 다음 5개 정규장 세션을 관찰하며 기존 `short`(1~3개월), `medium`(3~12개월), `long`(1년 이상)도 유지한다.

| 상단 항목 | 의미 |
|---|---|
| 기준가격 | 최신 확보 종가/지연 시세와 시각·통화; 실시간 현재가로 표현하지 않음 |
| 1차 목표주가 | 근거와 계산 방법이 있는 조건부 목표가격 및 상승여력 |
| 수익실현 검토 | 목표 도달 조건, 관찰기한·재평가 시각; 도달 예상기간은 검증 모델이 있을 때만 표시 |
| 하방·손익비 | 가설 무효화 가격/조건, 하락폭, 기준가격 대비 목표 상승여력과의 비율 |
| 매력도 1~100 | 검증된 결정론적 모델의 점수와 가감점 근거; 근거 신뢰도와 별도 |
| 한줄평 | 기회·성립 조건·핵심 위험 |

개인용 가격 공급자는 선정했고 본인 계정·키 연결이 남았다. 목표가·매력도/도달기간은 **후보 코드만 구현, 실증 미검증** 상태다. 출력 계약 `0.2.0`은 유지하며 실제 추천 수치 제공은 활성화하지 않는다. 가격 데이터가 없으면 스윙 블록은 사유와 함께 보류하고 기존 공시 리서치는 유지한다. 상세 기준은 [스윙 리포트 명세](docs/SWING_REPORT_SPEC.md)에 있다.

## 폴더 구성

```text
equity-research-lab/
├── AGENTS.md                       # AI 코딩 에이전트가 따라야 할 프로젝트 규칙
├── README.md                       # 목적 / 시작 가이드
├── .env.example                    # 구성값 템플릿 (실제 키 없음)
├── .gitignore
├── pyproject.toml                  # CLI/검증 의존성과 실행 진입점
├── research/                       # 선택 공시 입력·계산·검증·보고서 CLI
├── research_inputs/BE/             # 검토한 실제 SEC 선택 수치와 정성 문안
├── docs/
│   ├── PRD.md                      # 제품 요구사항과 MVP 경계
│   ├── ARCHITECTURE.md             # 데이터→분석→검증 흐름
│   ├── ANALYSIS_PLAYBOOK.md        # 주식/ETF/섹터 분석 방법론
│   ├── DATA_GOVERNANCE.md          # 데이터 계약·출처·갱신·라이선스
│   ├── COMPLIANCE_AND_SAFETY.md    # 사업화/자문 규제 체크포인트
│   ├── EVALUATION.md               # 품질 게이트와 골든 테스트
│   ├── SWING_REPORT_SPEC.md         # 5거래일 스윙 출력·산식·보류·검증 기준
│   ├── SEC_REPORT_PILOT.md           # 첫 로컬 SEC 보고서의 범위·실행·검증
│   ├── ROADMAP.md                  # 단계별 개발 계획
│   ├── DECISIONS.md                # 결정 로그와 미결 쟁점
│   ├── MVP_SPEC.md                 # 공개 베타 기능·수용 기준
│   ├── PUBLIC_ARCHITECTURE.md      # 실제 API/worker/DB 설계
│   ├── API_CONTRACT.md             # REST endpoint 및 상태 계약
│   ├── SOURCE_LICENSING.md         # 공개·AI 데이터 사용권 검증
│   └── OPERATIONS.md               # 비용/보안/운영·출시 조건
├── prompts/
│   ├── 00_system.md                # 상위 불변 원칙
│   ├── 10_equity_analyst.md        # 기업 분석
│   ├── 11_sector_analyst.md        # 섹터 분석
│   ├── 12_fund_analyst.md          # ETF/레버리지 ETF 분석
│   ├── 20_red_team.md              # 강제 반론 및 오류 탐지
│   └── 30_report_writer.md         # 사용자 보고서 구성
├── config/
│   ├── data_sources.yaml           # 연동 후보/권리 확인 상태
│   └── analysis_profile.yaml       # 분석 기간/가중치/갱신 정책
├── schemas/
│   └── research_report.schema.json # 버전 관리된 JSON 출력 계약
├── examples/
│   └── illustrative_report.json    # 실제 시장 자료 아님
└── tests/
    ├── golden_cases.jsonl          # 평가할 입력/불변 조건
    └── test_contract.py            # 스키마 및 fixture 검증
```

## 핵심 설계 원칙

- **계산과 해석 분리**: 성장률, 밸류에이션, 성과, 변동성 계산은 코드로 검증한다. 모델은 계산의 의미를 설명한다.
- **출처 우선**: 수치·날짜·사건 주장마다 근거 ID를 유지하며 리포트에서 출처를 표시한다.
- **'모름' 허용**: 데이터가 없거나 오래됐으면 추정치/미확인을 분리하고 확정적 추천을 삼간다.
- **조건부 시나리오**: 미래 주가와 확률을 근거 없이 확정하지 않는다. 각 시나리오에 전제와 무효화 조건이 있어야 한다.
- **상품 유형 식별**: 발행사 개별주식, 인덱스 ETF, 단일종목 레버리지 ETF는 다른 리스크와 지표를 쓴다.
- **검증 가능한 품질**: JSON 계약, 출처 검사, 적대적 검토, 고정 평가셋, 회귀 테스트가 필수다.

## 시작 순서

1. `docs/DECISIONS.md`에서 확정 조건과 아직 미결인 출시 차단 항목을 확인한다.
2. `docs/PRD.md`와 `docs/ANALYSIS_PLAYBOOK.md`의 MVP 범위를 확정한다.
3. 현재 범위의 개인 수집·저장·파생 분석 권리와 무료 계정 조건을 확인한다. 공개·외부 AI 이용은 별도다 (`docs/MARKET_PREPARATION.md`).
4. `prompts/`와 `schemas/`를 기준으로 **실제 데이터가 아닌 fixture**를 사용해 출력 형태를 먼저 테스트한다.
5. SEC 기반 데이터 어댑터 → 계산 엔진 → 분석 파이프라인 → 공개 API/작업 큐 → 웹 UI 순서로 구현한다. 가격 사용권 검토는 초기부터 병행하고, 스윙 가격/점수 기능은 별도 검증 후 활성화한다.

### 로컬 스키마 테스트

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

## 기술 스택 제안 (확정 아님)

수치 예측 ML 비교 도구를 추가했다. `python -m research.learned_research --synthetic`으로 별도 학습/오차 추정/최종 평가와 기존 EWMA 비교를 실행한다. 실제 BE 경로는 `python -m research.learned_research`이며 현재 `model_training` 권한 미확인으로 학습 전에 차단한다. [방법론·권한 검토·다음 단계](docs/LEARNED_PEAK_MODEL.md)를 참조한다. 합성 실험은 실제 BE 성능 검증이 아니다.

- Python / FastAPI / Pydantic / Pandas 또는 Polars: 수집·계산·API
- PostgreSQL: 정형 시계열 및 리포트 메타데이터; 원문은 객체 스토리지
- Redis + 태스크 워커: 데이터 갱신 및 비동기 리포트 생성 (**공개 베타에는 필요; CLI 단계에서는 생략 가능**)
- React / TypeScript: 결과 열람 및 출처 탐색 UI
- JSON Schema 기반 LLM Structured Outputs: 구조화 보고서 초안
- Pytest / 고정 eval 사례 / 관측성: 회귀 및 환각 방지

## 참고한 1차 자료

- SEC EDGAR API: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Fair Access: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- BLS 약관: https://www.bls.gov/developers/termsOfService.htm
- FRED AI 관련 제한: https://fred.stlouisfed.org/legal/
- OpenDART: https://opendart.fss.or.kr/intro/main.do
- KRX 데이터 이용: https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp
- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- 한국 금융위 유사투자자문 규제 안내: https://www.fsc.go.kr/no010101/82887
- SEC 레버리지 ETF 리스크: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec

**주의:** 교육/리서치 소프트웨어 설계 문서이며 투자 권유, 정확성 보장 또는 사업 관련 법률 의견이 아니다.
