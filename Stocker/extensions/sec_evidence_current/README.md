# SEC EvidenceAdapter 공식 신규 실행 경로

신규 계약 검증·조건부 계산은 `extensions.sec_evidence_current`를 사용한다.
검증기 구현은 `sec-evidence-adapter-v1.1`에 고정돼 있다. v1·v1.1·ImpactEngine의
역사 소스/방법/보고서는 변경하지 않는다. 이 facade는 새로운 경제 엔진이 아니다.

## 실행

```powershell
.\.venv\Scripts\python.exe -m extensions.sec_evidence_current --tickers BE MSFT XOM --as-of <UTC> --source-store <고정한-private-snapshot> --legacy-review-root <고정-review-copy> --review-store <고정-contract-copy> --out <private-output>
.\.venv\Scripts\python.exe -m extensions.sec_evidence_current --validate <current-report>
.\.venv\Scripts\python.exe -m extensions.sec_evidence_current --legacy-replay <old-report> --legacy-version v1
```

`--input`으로 제출하는 JSON은 `contract.REQUEST`를 모두 충족해야 한다.
`verification_version` 누락·v1·미지원 버전은 거절한다. 티커 CLI는 요청을 만들 때
명시적으로 v1.1을 넣는다. 이는 버전 없는 외부 요청을 v1로 fallback하는 동작과
다르다. v1.1 오류·미지원 의미·trust 부족이면 금액 null과 reason_codes를 반환하며
v1로 재시도하지 않는다. 검증된 0은 문자열 `"0"`이고 자료 부족은 JSON null이다.

## 공식 API와 경계

- `facade.request_for`: 버전이 명시된 요청 생성. 원문/기간/event-only 입력을 분리.
- `facade.authorize`: 닫힌 요청 검사 → 고정 v1.1 → 독립 리뷰/owner trust → 의미/관계/
  현재 적격성 → `sec-current-verification-v1` 결합 인증서.
- `bridge.calculate`: request/result/certificate와 외부 owner context를 모두 요구한다.
  인증서를 자체 승인으로 신뢰하지 않고 원문·receipt·trust·방법·period를 재계산한다.
- `facade.process`: 거절/오류를 안전한 BLOCKED 또는 WITHHELD/null로 표시한다.
  예상하지 못한 validator 오류의 error_class를 보존하므로 정상 검증으로 숨기지 않는다.
- `report.run/validate`: 버전·외부 owner pin·결과·한국어 보고서·파일 해시를 불변 재생.
- `replay.validate_legacy`: 명시한 원래 validator만 호출하며 `current_approval=False`.
- `facade.accept_review`: v1.1 의미 검사 후 새 `reports/private/sec_current_reviews/`
  namespace에만 append. 기존 사용자 SEC review registry는 읽기 전용이다.

인증서는 요청·검증 결과·source hash/incarnation·기간·event·owner pin·방법 해시를
묶는 **재생 가능한 결합**이다. 서명이 있는 사람 신원 증명이나 비밀 토큰이 아니다.
외부에서 seal을 다시 계산해도 새 bridge가 현재 원문과 외부 owner root로 재생하므로
원래와 다른 결과는 승인되지 않는다. legacy VERIFIED/old report를 인증서 대신
제출할 수 없고 Boolean·semantic_type·relationship_verified 입력으로 승격할 수 없다.

## Owner authority

`OwnerContext.load(policy_path, pinned_sha256, registry=...)`는 **호출 운영자 설정**이다.
익명 요청 JSON이나 보고서에서 이 값을 가져오면 안 된다. CLI의 `--owner-policy`와
`--owner-sha256`도 로컬 운영자가 제공하는 별도 인수다. 외부 API 인증 서버는 이번에
추가하지 않았으며, 미래 서비스는 이 설정을 서버가 관리해야 한다.

기존 닫힌 owner trust 계약을 재사용한다. 최초 가용 시각은 새 facade 전용 registry에
고정하며 owner-declared 과거 시각으로 소급하지 않는다. REAL namespace는 실제
시계만 허용하고, 기존 사용자 SEC trust registry를 수정하지 않는다. 정책/최초 수신
receipt 바이트 pin이 바뀌면 다시 검증해야 한다. 보고서 내부 allowlist는 root authority가
아니다. 외부 context 없이 기존 nonempty-owner 보고서를 재생하면 거절한다.

검토자 ID를 바꾼 복제본은 새 receipt hash이며 원래 owner pin을 상속하지 않는다.
단일 리뷰를 중복 나열해도 독립 두 사람으로 세지 않는다. 다만 owner가 거짓 신원이나
독립성을 직접 신뢰하도록 승인했다면 체크섬으로 실제 사람의 독립성을 입증할 수
없다. 이 신뢰 경계는 오프라인 신원/독립성 확인과 owner 운영 권한에 의존한다.

## Legacy 격리와 잔여 위험

원래 v1 CLI/함수, raw v1.1/Impact Python API는 역사 해시 보존을 위해 수정하지
않았다. **직접 import/호출은 여전히 가능하다.** facade를 추가했다고 모든 Python
우회가 제거된 것은 아니다. 새 공식 CLI/bridge의 gate 통과와 이 잔여 위험은 별도다.
기존 평가·작업을 강제로 마이그레이션하거나 전역 import hook으로 막지 않는다.

`boundary.py`와 저장소 루트의 `.github/workflows/sec-evidence-current.yml`은
새 코드가 raw 엔진을 직접 import/call하거나 상수 dynamic import하는지 검사한다.
보호된 원래 파일은 byte hash로 고정하고, 새 facade의 허용 경로도 symbol별로
명시한다. 테스트·읽기 전용 발견·명시적 역사 재생을 구분한다.

```powershell
.\.venv\Scripts\python.exe extensions/sec_evidence_current/boundary.py --root .
```

이 검사는 CI 정책이며 실행 중 Python sandbox가 아니다. 반사/동적 실행/정책 자체
편집/동일 프로세스의 임의 코드까지 막지 않는다. 배포 권한과 process isolation 없이
전역 우회 제거를 주장하지 않는다. 기존 원본 직접 진입점은 감사의
`residual_direct_calls`에 UNSAFE_BYPASS로 계속 표시한다.

## 운영과 검증 범위

수집기·LLM·유료 API·새 예약 작업이 없다. QA는 별도 private UUID에서 byte-exact
history/review 사본과 원본 raw hash를 고정한다. 공유 mutable state 변화는 별도
운영 감사이며 복원하거나 PASS로 바꾸지 않는다. v1.1은 지원 문법의 의미 규칙만
검증한다. 실제 독립 계약 검토가 없으면 VERIFIED/적격 0, 실제 금액 null이 정상이다.
SHADOW·점수/목표가/계획 보류·실제 시장 의미 정확도 NOT_MEASURED를 유지한다.
