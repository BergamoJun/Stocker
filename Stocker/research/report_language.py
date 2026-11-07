"""Reader-facing Korean explanations; internal codes stay in the JSON."""

from decimal import Decimal, ROUND_HALF_UP
import html


REASONS = {
    "INSUFFICIENT_NET_REWARD_RISK": "비용을 뺀 뒤 얻을 수 있는 이익이, 손실 기준까지의 위험에 비해 충분하지 않습니다.",
    "NONPOSITIVE_ESTIMATED_PATH_EV": "모형이 가정한 여러 가격 흐름의 평균을 보수적으로 계산하면 이익이 남지 않습니다.",
    "NONPOSITIVE_NET_TARGET_UPSIDE": "예상 목표까지 올라가더라도 매수·매도 비용을 빼면 이익이 남지 않습니다.",
    "INSUFFICIENT_EFFECTIVE_SUPPORT": "예측 계산을 뒷받침하는 관측 자료가 연구 기준보다 부족합니다.",
    "TARGET_TIMING_UNAVAILABLE": "목표에 먼저 도달하는 흐름이 충분하지 않아 도달 시기를 계산하지 못했습니다.",
    "EXPIRED_REFERENCE_PLAN": "가격 계산의 사용 기한이 지나 새 자료로 다시 계산해야 합니다.",
    "NO_POSITIVE_LONG_PLAN": "이 모형에서 현재 가격보다 높은 목표와 유효한 손실 기준을 함께 만들지 못했습니다.",
    "NO_FORECAST": "가격 예측 결과가 없어 비용·위험 조건을 검사하지 못했습니다.",
    "INVALID_OR_MISSING_FORECAST_INPUT": "계산에 필요한 자료가 없거나 서로 맞지 않아 판단을 보류했습니다."
}


def escaped(value):
    return html.escape(str(value)).replace("[", "\\[").replace("]", "\\]").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def percent(value):
    if value is None:
        return "확인되지 않음"
    amount = Decimal(str(value))
    if not amount.is_finite():
        return "확인되지 않음"
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded == 0 and amount != 0:
        return "0.01% 미만의 " + ("이익" if amount > 0 else "손실")
    return ("+" if rounded > 0 else "") + f"{rounded:.2f}%"


def screen_explanation(screen):
    if screen is None or screen.get("screen_decision") == "unavailable":
        return "필요한 계산 자료를 확인하지 못했습니다."
    if screen["screen_decision"] == "stand_aside":
        return "현재 가격 계산은 비용·위험 조건을 충족하지 못했습니다."
    return "비용·위험의 수치 조건은 충족했습니다. 실제 수익성이 검증된 추천이라는 뜻은 아닙니다."


def trade_markdown(result):
    screen = result.get("screen")
    lines = ["# BE 가격·비용·위험 점검", "", "## 먼저 읽을 결론", "", screen_explanation(screen),
        "이 문서는 앞으로 약 5거래일을 살펴보는 연구 자료입니다. 실제 매매 결과로 예측력을 확인하는 작업과 최신 정보 검토는 아직 진행 중입니다.",
        "", f"작성 기준: {result['as_of']} (UTC)", "", "## 무엇을 확인했나요?", "",
        "1. 예상 목표까지 올라도 매수·매도 비용을 빼고 이익이 남는가?",
        "2. 손실 기준까지의 위험에 비해 예상 이익이 충분한가?",
        "3. 모형이 만든 여러 가격 흐름을 종합해도 평균적으로 이익이 남는가?", ""]
    if screen:
        lines += [f"사용한 가격 자료의 거래일: {screen['reference_session']}. 실시간 가격이 아니라 해당 거래일의 종가를 기준으로 계산했습니다.",
            "", "## 가격 조건의 판단 이유", ""]
        lines += ["- " + REASONS.get(code, "추가 확인이 필요한 계산 조건이 있습니다.") for code in screen["reason_codes"]] or ["검사한 수치 조건은 통과했습니다. 전체 투자 추천도 검토는 별도입니다."]
        lines += ["", "## 어떻게 미래 가격을 계산했나요?", "",
            "기본 엔진은 과거 가격의 변화폭을 이용해 앞으로 5거래일에 가능한 가격 흐름을 계산합니다. 그 기간의 예상 최고가가 모이는 구간을 참고해 목표를 만들고, 목표보다 손실 기준에 먼저 닿는 경우도 계산합니다.",
            "계산으로 만든 가격 흐름의 개수는 실제로 예측을 검증한 횟수가 아닙니다. 아래 평균 손익도 모형 가정에 따른 계산값이며, 실제 미래 수익을 확인한 값은 아닙니다.",
            "", "## 계산 결과를 읽는 방법", "",
            "매수·매도 비용은 수수료와 불리한 체결 가격 등을 묶어 가정한 값입니다. 1bp는 0.01%이며, 기본 심사는 매수와 매도 때 각각 0.25%(25bp)를 가정합니다.",
            "손익비 1은 예상 손실 1원당 예상 이익도 1원이라는 뜻입니다. 이 계산은 현재 기준 종가에서 출발하며 다음 거래일의 실제 시작 가격은 아직 모릅니다.", "",
            "| 각 매수·매도의 비용 가정 | 목표까지 오를 때 비용 후 이익 | 예상 손실 1원당 예상 이익 | 여러 가격 흐름의 계산상 평균 손익 |",
            "|---|---|---|---|"]
        for row in screen["metrics"]:
            cost = Decimal(row["cost_bps_per_side"]) / 100
            ratio = Decimal(row["net_reward_risk"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            lo, hi = row["estimated_path_ev_pct_bounds"]
            lines.append(f"| 각각 {cost:.2f}% | {percent(row['net_reference_to_target_pct'])} | {ratio:.2f}원 | {percent(lo)} ~ {percent(hi)} |")
        lines += ["", "평균 손익의 두 끝값은 같은 날 목표와 손실 기준에 모두 닿았을 때 어느 쪽이 먼저인지 모르는 상황을 반영합니다. 미래 수익의 보장 범위나 실제 적중 확률은 아닙니다."]
    else:
        lines += ["입력 자료를 확인하지 못해 가격 판단을 보류했습니다. 오래된 자료를 현재 가격처럼 사용하지 않습니다."]
    lines += ["", "## 아직 확인해야 하는 것", "",
        "- 실제 시장에서 예측을 먼저 저장하고, 이후 5거래일의 결과와 비교해야 합니다.",
        "- 뉴스·공시·시장 흐름을 함께 검토해야 합니다. 이 문서의 가격 계산만으로 전체 투자 매력도를 판단하지 않습니다.",
        "- 정식 목표가·투자 매력도 점수·수익 실현 예상일은 검증이 끝날 때까지 제공을 보류합니다.", ""]
    return "\n".join(lines)
