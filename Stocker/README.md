# Equity Research Lab

종목/섹터를 입력하면 **출처가 추적 가능하고 재현 가능한** 투자 리서치 보고서를 생성하는 서비스의 사전설계 프로젝트.

> Status: Pre-development (v0.2). **데이터 연동, 모델 API, 웹서비스는 아직 구현되지 않았다.** 현재 폴더는 제품·API·아키텍처·출력·품질 명세를 정의한다.

## v0.2 확정 조건 (2026-11-06)

- 누구나 접근하는 **공개 무료 서비스**
- **미국 상장 주식과 ETF** 우선
- **종가(EOD)·지연 시세** 중심. 단, 공개 전시와 AI 분석 사용권이 확인된 가격 제공자가 없으면 가격 기능은 비활성화
- SEC 공시 기반 기업·재무 리서치를 첫 번째 end-to-end 범위로 구현
- 익명 리포트 조회와 생성 제한, 중복 작업 병합, 비동기 큐, 월 비용 상한을 초기부터 적용
- FRED API의 AI 관련 이용 제한 때문에 직접 연결은 보류하고 BLS·BEA 등 원천 기관 사용을 검토
- 구체 명세: docs/MVP_SPEC.md, docs/PUBLIC_ARCHITECTURE.md, docs/API_CONTRACT.md, docs/SOURCE_LICENSING.md, docs/OPERATIONS.md

## 제품 목표

1. 미국 개별주식, 일반 ETF, 레버리지/인버스 ETF, 산업 섹터를 **서로 다른 분석 방법론**으로 다룬다.
2. 시장/재무 데이터와 뉴스·공시를 수집하고 **출처, 공표일, 조회시각, 기준시각**을 보존한다.
3. 검증된 수치 계산을 거친 후 AI가 강세·중립·약세 논리와 반례를 정리한다.
4. 판단의 근거, 반증 조건, 불확실성, 데이터 공백을 보고서에서 명확히 노출한다.
5. 사용자의 실제 포지션을 고려하는 기능은 별도 권한/규제 검토 후 확장한다.

## 폴더 구성

```text
equity-research-lab/
├── AGENTS.md                       # AI 코딩 에이전트가 따라야 할 프로젝트 규칙
├── README.md                       # 목적 / 시작 가이드
├── .env.example                    # 구성값 템플릿 (실제 키 없음)
├── .gitignore
├── pyproject.toml                  # 스키마 테스트 의존성만 정의
├── docs/
│   ├── PRD.md                      # 제품 요구사항과 MVP 경계
│   ├── ARCHITECTURE.md             # 데이터→분석→검증 흐름
│   ├── ANALYSIS_PLAYBOOK.md        # 주식/ETF/섹터 분석 방법론
│   ├── DATA_GOVERNANCE.md          # 데이터 계약·출처·갱신·라이선스
│   ├── COMPLIANCE_AND_SAFETY.md    # 사업화/자문 규제 체크포인트
│   ├── EVALUATION.md               # 품질 게이트와 골든 테스트
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
3. 사용할 데이터 제공자의 공개·AI 활용·저장 권리, 요금·지연시간을 확인한다 (`docs/SOURCE_LICENSING.md`).
4. `prompts/`와 `schemas/`를 기준으로 **실제 데이터가 아닌 fixture**를 사용해 출력 형태를 먼저 테스트한다.
5. 그 다음 데이터 어댑터 → 계산 엔진 → 분석 파이프라인 → 공개 API/작업 큐 → 웹 UI 순서로 구현한다.

### 로컬 스키마 테스트

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

## 기술 스택 제안 (확정 아님)

- Python / FastAPI / Pydantic / Pandas 또는 Polars: 수집·계산·API
- PostgreSQL: 정형 시계열 및 리포트 메타데이터; 원문은 객체 스토리지
- Redis + 태스크 워커: 데이터 갱신 및 비동기 리포트 생성 (**MVP에서는 선택 사항**)
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
