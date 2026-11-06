# 데이터 계약·신뢰성·사용권

## 데이터 우선순위

1. 규제기관의 공시(미국 SEC EDGAR, 한국 금융감독원 OpenDART)
2. 발행사의 공식 실적발표·IR·ETF 공시/상품설명서
3. 사용 허가를 받은 가격·지수·기업행동(corporate action) 벤더
4. AI 및 공개 이용권이 확인된 공식 원천 거시통계(예: BLS·BEA; FRED API는 현재 연결 보류)
5. 검증 가능한 뉴스/애널리스트 의견(사실과 견해 구분)
6. 소셜 미디어/커뮤니티는 **심리·주장 확인용 보조**로만 활용

## 최소 데이터 레코드

| 필드 | 의미 |
|---|---|
| `source_id` | 안정적인 로컬 증거 식별자 |
| `instrument_id` | ticker만 아닌 거래소·종류 등 안정적 식별 키 |
| `source_url` | 원문 주소(실제 존재·내용 일치 검증) |
| `publisher` / `source_type` | 발행기관 및 권위 등급 |
| `published_at` | 처음 대중에 공개된 일시 |
| `retrieved_at` | 시스템이 데이터를 가져온 일시 |
| `as_of` | 리포트가 참조하는 과거 시점 컷오프 |
| `effective_period` | 재무 분기·기간 또는 시세 거래일 |
| `currency`, `unit`, `adjustment` | USD/KRW, 주·백만 단위, 수정주가 여부 |
| `raw_hash`, `revision_id` | 원문 스냅샷 및 데이터 정정 추적 |
| `license_tag`, `redistribution_allowed` | 이용권·재표시 가능 범위 |

**중요:** `published_at <= as_of`가 충족되지 않으면 해당 지식을 과거 시점 리포트에서 제거한다. 수정된 경제지표는 당시 관측 가능한 값과 최종 수정치를 구분한다. 공급자별 AI 사용권 확인 없이는 API 연동하지 않는다.

## 신규 커넥터 필수 체크

- 무료/유료, 개인/사업·재배포·상업적 2차 가공 허용 범위
- 종가/지연/실시간 구분 및 시세 타임존; 데이터 재사용 권리
- API key, 초당 호출·일일 quota, 캐시 가능성, 장애 시 정책
- 분할·배당·배당락/거래정지/상장폐지와 심볼 변경 보정
- 컨센서스·시세·보유종목 데이터의 제공 시점과 빈도

`config/data_sources.yaml`은 **후보 목록**이며, 연결이 완료됐다는 의미가 아니다. 웹에서 찾을 수 있는 가격이 있다고 해서 유료/공개 서비스가 그 가격을 배포할 권리가 생기는 것은 아니다.

## 신선도 정책(가안)

- 실시간 가격: 해당 계약 및 거래소 지연 요건에 따라 표기. 권리 확인 전 **미지원**.
- 종가: 최근 완료된 정규장 세션이 무엇인지 명시. 휴일/시간외 구분.
- 재무 공시: 마지막 가용 발표일·해당 회계기간을 명시. 다음 분기 실적을 추측하지 않는다.
- ETF 편입비중: 운용사 최신 발표일 표시; 오래된 구성으로 현재 노출을 단정하지 않는다.
- 뉴스: 기사 작성/수정일, 사건 실제 발생일, 검색일을 분리.
- 거시: 원 시리즈 단위·조정·주기·수정 이력을 보존. 공급자 약관상 AI 사용과 캐싱이 허용되는지 별도 확인.

## 실패 정책

- 필수 소스 실패 → `data_status.missing` + `limitations`를 생성하고 해당 항목에 대한 확정적 서술 금지.
- 서로 모순되는 소스 → 원자료·기준일·단위를 대조, 해결 전 한 값으로 병합하지 않음.
- URL 불명/인용 불일치 → 출처 주장 제거하거나 리포트 실패 처리.

## 공식 참고

- SEC API: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC rate/fair access: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- BLS: https://www.bls.gov/developers/termsOfService.htm
- BEA: https://apps.bea.gov/API/docs/index.htm
- FRED AI/캐싱 관련 제한: https://fred.stlouisfed.org/legal/
- OpenDART: https://opendart.fss.or.kr/intro/main.do
- KRX 데이터 수신·이용 계약: https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp
- 국내 공공데이터 상업적 이용 주의: https://www.data.go.kr/data/15094805/openapi.do
