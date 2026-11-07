# 5거래일 고점 수치 예측 ML 비교 모델

2026-11-07 구현. **학습·시간순 비교 실험 도구를 완성했으며 BE 실증 예측 성능은 미검증이다.** LLM 파인튜닝이나 외부 모델 API를 사용하지 않는다. 월 데이터 예산 $0, 개인 로컬 분석 범위를 유지한다.

첫 합성 실험 및 실제 입력 권한 차단 결과: [LEARNED_PEAK_RESULTS_20261107.md](LEARNED_PEAK_RESULTS_20261107.md).

기존 ATR 정책과 `peak_model_policy.json`은 변경하지 않는다. 새 `config/learned_peak_policy.json`은 기존 정책 해시를 고정 참조하고 별도 방법론 `five-session-ridge-residual-v1`을 사용한다. 가격 단위 산술/체결/비용은 기존 Decimal 계산을 재사용하며 회귀·표준화는 NumPy 부동소수점으로 계산한다.

## 학습 대상과 계산식

입력은 기준일까지의 가격에서 계산한 8개 특징이다. 1/5/20일 ATR 정규화 수익, SMA20/60 거리, 20일 가격대 위치, 과거 대비 상대 ATR, 상대 거래량 로그다. 미래 뉴스·가격을 특징에 넣지 않는다. 이번 버전은 **가격 전용 비교 모델**이며 뉴스·SEC·섹터·거시 특징을 학습했다는 의미가 아니다.

학습 구간에서만 평균 μ와 표준편차 s를 산출하고 z=(x−μ)/max(s,0.001), φ(x)=[1,z,z²]로 변환한다. 반응값 Y는 이후 5거래일의 20개 OHLC 각각을 (가격−기준종가)/기준 ATR14로 표현한 경로다.

회귀 계수는 B=(ΦᵀΦ+λD)⁻¹ΦᵀY, λ=10, D=diag(0,1,…,1)이다. 실제 구현은 역행렬을 직접 만들지 않고 선형 방정식을 푼다. 17×20개 계수가 자료에서 학습된다. 신경망이나 언어모델이어야 미래를 예측할 수 있는 것은 아니다.

별도 오차 추정 구간에서 Eᵢ=Yᵢ−φ(xᵢ)B를 구한다. 현재 특징의 예측 경로는 φ(x현재)B+Eᵢ이다. 5일 전체 오차 경로를 묶어서 재사용하므로 날짜 간 오차 관계를 보존한다. H=max(예측H,O,C), L=min(예측L,O,C)로 일봉 포락선을 복원한다. 가격이 0 이하인 경로가 있으면 전체 후보를 보류하고 불리한 경로만 제거하지 않는다. 표준화 특징의 절댓값이 6을 넘으면 학습 범위 밖으로 보류한다.

각 경로의 향후 5일 최고 일봉 고가와 최초 최고일을 추출해 고점 가격대·일자 결합 분포를 만든다. 기존 0.5ATR 구간, 최빈 가격 구간 하단 1/4 지점 목표, 하방 20% 분위 무효화, 목표/무효화 선후·갭·동일 일봉 모호성·10/25bp 편도 비용 계산을 재사용한다. 목표 도달일과 최고 고점일은 서로 다르다. 경험적 질량·20/50/80 분위는 보정된 확률이나 보장된 구간이 아니다.

## 시간순 분리와 비교

기준일 간격 5세션으로 겹치지 않는 미래 5일 라벨을 생성한다. 원래 사례의 앞 60%를 학습, 다음 20%를 오차 추정, 나머지를 최종 평가로 나눈다. 앞 구간 라벨 종료가 다음 구간 첫 기준일과 같거나 늦으면 제거한다. 표준화/계수는 학습 구간만, 오차 경로는 오차 추정 구간만 사용한다. 최종 평가 정답으로 계수·λ·가중치를 조정하지 않는다.

비교 대상은 기존 고정 EWMA 경로 모형, 학습 구간의 무조건부 경로 분포, ATR 목표, 동일 5세션 보유다. 고점 중앙값 MAE(ATR), 분위 예측 오차(pinball loss), 고점 구간×고점일 joint Brier, 가격 구간 포함 빈도, 조건부 고점일 오차를 저장한다. 공통 예측 가능 모집단을 사용하고 실패/보류 수를 남긴다. 체결 모호성은 공통으로 제외한 수와 원래 사례를 모두 보존한다. 표본 수 게이트 통과는 성능 승인·실제 시장 검증 완료를 뜻하지 않는다.

과거 일봉을 현재 내려받아 학습해도 당시 이용 가능했던 수정 전 정보가 복원되지는 않는다. 현재 조회본 실험은 계속 진단이며 `point_in_time_validated=false`, `real_market_performance_validated=false`, 보고서 목표·추천 점수·기간·검증 ID는 null이다.

## 재현 실행

프로젝트 디렉터리에서 실행한다. 네트워크 요청이나 유료 API 호출이 없다.

```powershell
./.venv/Scripts/python.exe -B -m research.learned_research --synthetic
./.venv/Scripts/python.exe -B -m research.learned_research
./.venv/Scripts/python.exe -B -m research.learned_research --prices <snapshot.json> --price-entitlement <reviewed-entitlement.json>
```

첫 명령은 명시적 `ILLUSTRATIVE — NOT LIVE MARKET DATA` 합성 자료 3종(신호, 무작위, 학습 정답 섞기)으로 학습·비교한다. 두 번째는 저장된 최신 BE 입력을 선택한 뒤 **가격 파일을 읽거나 해시하기 전에** `model_training`을 검사한다. 현재 Massive 권한이면 차단 상태를 저장하고 종료한다. 세 번째는 검토된 동일 가격 계약의 자료를 연결하는 오프라인 경로다. 공급자·권한 ID, USD, 분할 조정 기준, 거래소 세션, 실제 관측시각을 그대로 검사한다. 실제 입력의 학습 권한을 임의로 허용으로 바꾸면 안 된다.

결과·계수·오차 경로·입력/코드/정책 해시는 ignored `reports/private/learned_research/<실행시각>/`에만 저장한다. JSON 계약은 `schemas/learned_research.schema.json`이다. 등록 파일을 결과 계산 전에 저장한다. JSON 모델을 사용하며 pickle 역직렬화는 사용하지 않는다. NumPy 버전과 계수를 기록하지만 모든 운영체제/BLAS 버전에서 비트 단위 일치까지 보장하지는 않는다.

## 무료 실데이터 학습 권한 검토

2026-11-07 공식 문서 재확인 결과는 `config/model_training_sources.json`에 기록했다. 이 후보 목록 자체는 사용권이 아니다.

| 공급자 | 확인 내용 | 현재 결론 |
|---|---|---|
| Massive Basic | 시장 데이터 약관 §5(g): 학습/미세조정/증류는 명시 허용 및 제3자 요건 충족 필요 | 기존 개인용 계정만으로 학습 권한 확인 불가 |
| SimFin Free | 무료 API/일괄 다운로드, 5년 지연 이력, 홈페이지 개인 ML 사용 사례 | 유망 후보. 실제 BE OHLC 범위와 다운로드 자료·IEX 등 원천 조건 미확인 |
| Twelve Data | §2.2(d) 비표시 사용은 구독 등급에 따라 허용, §3 원천 조건 별도 | 무료 등급의 해당 학습 범위 미확인 |
| FMP | 무료 호출량과 AI 연결 안내 | 실제 가격 자료의 무료 학습 허용을 확인한 것은 아님 |

SimFin의 약관상 명시된 Open Dataset은 프로필/회사 식별 정보다. 가격 전체를 무제한 공개 데이터로 간주하지 않는다. 라이브러리의 MIT 라이선스, 데이터셋 업로더의 CC0 표기, 공급자 마케팅 문구만으로 원천 주가 학습 권리를 인정하지 않는다. 가입·결제·공급자 메시지 전송은 진행하지 않았다.

근거: [Massive 시장 데이터 약관](https://massive.com/legal/market-data-terms-of-service), [SimFin 약관](https://www.simfin.com/en/legal-advice/), [무료 플랜](https://www.simfin.com/en/prices/), [Twelve Data 약관](https://twelvedata.com/terms), [FMP FAQ](https://site.financialmodelingprep.com/faqs).

## 사용 범위 확인용 문의 초안 — 미전송

아래는 검토용 초안이다. 자동 발송하지 않는다.

> I have a personal, nonprofessional, noncommercial project with a USD 0 monthly data budget. May I download and retain split-adjusted daily OHLCV for Bloom Energy (BE), train a local supervised numerical regression model to forecast the next five trading sessions, retain model parameters and residuals, and inspect derived targets and scores privately? No public redistribution, external LLM input, execution service, or resale is involved. Please confirm whether your free plan expressly permits this use, which underlying exchange/source restrictions apply, the permitted history and retention, and whether any separate agreement or fee is required. If it is not permitted at USD 0, please state that explicitly.

## 다음 순서

1. 실제 BE OHLCV를 이 용도로 학습·저장할 수 있는 무료 권한과 데이터 범위를 확인한다.
2. 같은 고정 정책으로 실제 가격 비교 진단을 실행한다. 자료 부족·보류·비용 효과를 포함한다.
3. 예측을 진입 전 고정 기록하고 관측 결과를 축적한다. 실제 시장의 오차/기간/성능 및 확률 보정을 검토한다.
4. 원문 링크와 검토된 사건 시각·관측 시각을 보존한 뉴스, 공시, 시장/섹터 특징을 별도 버전으로 추가하고 가격 전용 모형과 비교한다. 최신 뉴스 가중치 40점은 유지하되 자료 결손은 중립으로 대체하지 않는다.

현재 엔진의 토큰 사용과 모델 API 비용은 0이다. 학습 데이터 사용권 문제는 LLM 파인튜닝 비용과 별개다.
