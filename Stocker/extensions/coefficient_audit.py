"""Resumable fixed price-coefficient association audit, no fitting or final data."""

from collections import Counter, defaultdict
from decimal import Decimal, localcontext
import math
from pathlib import Path
import random
import time
import json

from research import panel_job, historical_panel, panel_prices, panel_diagnostics, peak_simulation, trade_screen
from research.local_files import atomic_json
from research.panel_transport import disk_name
from research.rights import require_entitlement, price_uses
from research.sec_report import ROOT
from research.sec_transport import now
from research.swing_models import atr14
from research.validation import digest, stable_hash
from . import weighted_engine, weighted_features as f


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def policy():
    value=read(Path(__file__).with_name("followup_policy.json"))["coefficient_audit"]
    if value['permitted_phases']!=['development','validation'] or any(value[k] for k in ('fit_or_select_coefficients','current_news_or_financial_backfill','final_holdout_unlocked')): raise ValueError('AUDIT_LOCK_OR_FITTING_BOUNDARY_CHANGED')
    return value


def factors(case, prefix, settings=None):
    """Features see only the source prefix and frozen forecast, never outcomes."""
    settings = settings or weighted_engine.policy(); p = policy()
    if not prefix or case["reference_session"] != prefix[-1].session: raise ValueError("AUDIT_PREFIX_REFERENCE_MISMATCH")
    forecast = case["forecast"]
    if forecast["reference_session"] != prefix[-1].session or f.number(forecast["reference_price"]) != prefix[-1].close: raise ValueError("AUDIT_FORECAST_REFERENCE_MISMATCH")
    ids = case["source_ids"]; result = {}
    def put(key,value=None,raw=None,reason=None): result[key]=f.factor(key,settings,value,raw=raw,source_ids=ids,reason=reason)
    with localcontext() as context:
        context.prec=60
        close=prefix[-1].close; atr=atr14(prefix); avg20=sum((b.close for b in prefix[-20:]),Decimal(0))/20; avg60=sum((b.close for b in prefix[-60:]),Decimal(0))/60
        distance=(close-avg20)/atr; ret5=(close/prefix[-6].close-1)*100
        put("trend_distance",f.clip(distance)); put("trend_structure",f.clip((avg20-avg60)/atr))
        put("momentum_five",f.clip(ret5/(atr/close*100*Decimal(5).sqrt())))
        volume=sum((b.volume for b in prefix[-21:-1]),Decimal(0))/20
        if volume>0: put("volume_confirmation",f.sign(close-prefix[-2].close)*f.clip(prefix[-1].volume/volume-1,0,1))
        else: put("volume_confirmation",reason="ZERO_PRIOR_VOLUME")
        a=settings["anchors"]
        put("overextension",-f.clip((distance-f.number(a["extension_atr_free"]))/f.number(a["extension_atr_span"]),0,1))
        screen=trade_screen.assess(forecast,as_of=case["actual_evaluation_at"],source_ids=ids,is_illustrative=case["is_illustrative"],mode="historical_diagnostic")
        if forecast["plan"] and screen["metrics"]:
            row=next(m for m in screen["metrics"] if m["cost_bps_per_side"]==p["primary_cost_bps_per_side"])
            put("net_target_upside",f.clip(f.number(row["net_reference_to_target_pct"])/f.number(a["net_upside_pct"])))
            put("path_ev_lower",f.clip(f.number(row["estimated_path_ev_pct_bounds"][0])/f.number(a["path_ev_pct"])))
            put("net_reward_risk",f.clip((f.number(row["net_reward_risk"])-f.number(a["reward_risk_neutral"]))/f.number(a["reward_risk_span"])))
            put("peak_concentration",2*f.number(forecast["peak"]["estimated_modal_bin_mass"])-1)
        for key in p["feature_ids"]:
            if key not in result: put(key,reason="NO_USABLE_PROFIT_FORECAST")
    return result


def compact(case, profile, ref, bars, calendar, *, replay=True, illustrative=False):
    clean=panel_diagnostics.compact_case(case,profile,ref,illustrative)
    base={"cik":profile["cik"],"sector_id":profile["sector_id"],"phase":ref["phase"],"reference_session":ref["reference_session"],"status":"unavailable","reason":case.get("reason_code")}
    if case["status"]=="unavailable": return base
    index=next((i for i,b in enumerate(bars) if b.session==ref["reference_session"]),None)
    if index is None or index<59: return {**base,"reason":"PREFIX_UNAVAILABLE"}
    if replay:
        rebuilt=historical_panel.evaluate_case(bars,calendar,ref,as_of=case["actual_evaluation_at"],source_ids=case["source_ids"],is_illustrative=illustrative)
        if rebuilt!=case: raise ValueError("AUDIT_SOURCE_CASE_REPLAY_CHANGED")
    values=factors(case,bars[:index+1])
    if any(v["status"]!="available" for v in values.values()): return {**base,"reason":"PRICE_FACTOR_SUBSET_INCOMPLETE"}
    primary=next(c for c in clean["cost_scenarios"] if c["cost_bps_per_side"]==policy()["primary_cost_bps_per_side"])
    with localcontext() as context:
        context.prec=60
        weighted=sum((f.number(v["signed_points"]) for v in values.values()),Decimal(0))
        equal=sum((f.number(v["value"]) for v in values.values()),Decimal(0))/len(values)
    return {**base,"status":"ready","reason":None,"weighted_signed_signal":str(weighted),"equal_signed_signal":str(equal),
        "hold_net_return_pct":primary["five_session_hold_net_return_pct"],"unfiltered_net_return_bounds":primary["unfiltered_net_return_pct_bounds"],"screen_decision":clean["screen_decision"]}


def ranks(values):
    result=[0.0]*len(values); order=sorted(range(len(values)),key=lambda i:values[i]); start=0
    while start<len(order):
        end=start+1
        while end<len(order) and values[order[end]]==values[order[start]]: end+=1
        rank=(start+end-1)/2+1
        for i in order[start:end]: result[i]=rank
        start=end
    return result


def spearman(a,b):
    if len(a)!=len(b) or len(a)<2: return None
    x,y=ranks(a),ranks(b); mx=sum(x)/len(x); my=sum(y)/len(y)
    covariance=sum((u-mx)*(v-my) for u,v in zip(x,y)); variance=sum((u-mx)**2 for u in x)*sum((v-my)**2 for v in y)
    return covariance/math.sqrt(variance) if variance>0 else None


def phase_summary(rows, refs, settings=None):
    settings=settings or policy(); usable=[r for r in rows if r["status"]=="ready"]; by_date=defaultdict(list)
    for row in usable: by_date[row["reference_session"]].append(row)
    daily=[]
    for ref in refs:
        group=by_date[ref["reference_session"]]
        if len({r["cik"] for r in group})<settings["minimum_issuers_per_date"]:
            daily.append({"reference_session":ref["reference_session"],"status":"insufficient_issuers","issuers":len(group)}); continue
        outcome=[f.number(r["hold_net_return_pct"]) for r in group]
        weighted=spearman([f.number(r["weighted_signed_signal"]) for r in group],outcome); equal=spearman([f.number(r["equal_signed_signal"]) for r in group],outcome)
        daily.append({"reference_session":ref["reference_session"],"status":"available" if weighted is not None and equal is not None else "constant_signal_or_outcome","issuers":len(group),"weighted_spearman":weighted,"equal_spearman":equal,"paired_difference":weighted-equal if weighted is not None and equal is not None else None})
    available=[r for r in daily if r["status"]=="available"]
    bounds=[]
    for block in [settings["primary_block_length"]]+settings["sensitivity_block_lengths"]:
        item={"block_length":block,"status":"withheld_insufficient_time_blocks","paired_difference_interval":None,"nominal_nonoverlapping_blocks":len(available)//block}
        if len(available)==len(refs) and len(available)>=settings["minimum_reference_dates"] and len(available)//block>=settings["minimum_nonoverlapping_blocks"]:
            values=[r["paired_difference"] for r in available]; rng=random.Random(settings["seed"]+block); means=[]
            for _ in range(settings["bootstrap_replications"]):
                sample=[]
                while len(sample)<len(values):
                    start=rng.randrange(len(values)-block+1); sample.extend(values[start:start+block])
                means.append(sum(sample[:len(values)])/len(values))
            means.sort(); item.update(status="exploratory_pointwise_interval",paired_difference_interval=[means[int(.025*(len(means)-1))],means[int(.975*(len(means)-1))]])
        bounds.append(item)
    return {"expected_slots":len(rows),"available_slots":len(usable),"missing_slots":len(rows)-len(usable),"reason_counts":dict(Counter(r["reason"] for r in rows if r["reason"])),
        "expected_reference_dates":len(refs),"available_reference_dates":len(available),"daily":daily,"block_sensitivity":bounds,
        "mean_weighted_spearman":sum(r["weighted_spearman"] for r in available)/len(available) if available else None,
        "mean_equal_spearman":sum(r["equal_spearman"] for r in available)/len(available) if available else None,
        "independent_sample_count":None,"coefficient_selection_performed":False,"released_score":None}


def source_status(job_path):
    job=panel_job.check(Path(job_path)); folder=Path(job_path).parent; state=read(folder/"state.json")
    return job,state,folder


def selected_profiles(cohort):
    if len({p['cik'] for p in cohort['selected']})!=len(cohort['selected']): raise ValueError('AUDIT_DUPLICATE_ISSUER')
    if any(p['symbol_partition'] not in {'development_and_validation','unseen_symbol_final_only'} for p in cohort['selected']): raise ValueError('AUDIT_UNKNOWN_PARTITION')
    return [p for p in cohort['selected'] if p['symbol_partition']=='development_and_validation']


def step(job_path, output, *, max_issuers=100, finish_before=None):
    """Wait for the whole source, then checkpoint issuer replays without final data."""
    job,state,folder=source_status(job_path)
    if state["status"] not in {"complete","complete_with_gaps"} or state["stage"]!="done": return {"status":"waiting_for_full_panel","source_status":state["status"],"completed_issuers":0}
    record=read(folder/"price_entitlement.json"); require_entitlement(record,price_uses("local_research"),now())
    cohort_path=folder/"cohort.json"
    if digest(cohort_path)!=state["cohort_sha256"]: raise ValueError("AUDIT_COHORT_CHANGED")
    cohort=read(cohort_path); profiles=selected_profiles(cohort)
    refs=historical_panel.references(); destination=Path(output).resolve()
    if not destination.is_relative_to((ROOT/"reports/private").resolve()): raise ValueError("PRIVATE_COEFFICIENT_AUDIT_REQUIRED")
    destination.mkdir(parents=True,exist_ok=True)
    fingerprint={"job_sha256":digest(job_path),"cohort_sha256":digest(cohort_path),"method_sha256":digest(Path(__file__)),"policy_sha256":digest(Path(__file__).with_name('followup_policy.json')),"weighted_policy_sha256":digest(Path(__file__).with_name('weighted_policy.json'))}
    manifest=destination/"manifest.json"
    if manifest.exists() and read(manifest)!=fingerprint: raise ValueError("AUDIT_CHECKPOINT_METHOD_CHANGED")
    if not manifest.exists(): atomic_json(manifest,fingerprint)
    processed=0
    for profile in profiles:
        target=destination/"issuers"/(profile["cik"]+".json")
        if target.exists(): continue
        if processed>=max_issuers or finish_before is not None and time.monotonic()>=finish_before: break
        ticker=profile["symbol"]; case_dir=folder/"evaluation"/disk_name(ticker); source_hashes={}
        rows=[]
        if state.get("stock_results",{}).get(ticker,{}).get("status")!="ready" or ticker not in state.get("evaluated",[]):
            rows=[{"cik":profile["cik"],"sector_id":profile["sector_id"],"phase":r["phase"],"reference_session":r["reference_session"],"status":"unavailable","reason":"SOURCE_STOCK_UNRESOLVED"} for r in refs]
        else:
            price_path=folder/"prices"/disk_name(ticker)/"prices.json"; manifest_value=read(case_dir/"manifest.json")
            if manifest_value!={"price_sha256":digest(price_path),"profile_sha256":stable_hash(profile),"engines":historical_panel.engine_hashes(),"phases":["development","validation"],"pilot":False}: raise ValueError("AUDIT_SOURCE_MANIFEST_CHANGED")
            _,bars,calendar=panel_prices.load(price_path,profile,record,now())
            expected={r["reference_session"]+".json" for r in refs}
            if any(p.name not in expected for p in (case_dir/"cases").glob("*.json")): raise ValueError("AUDIT_UNEXPECTED_OR_FINAL_CASE_FILE")
            source_hashes[str(price_path)]=digest(price_path)
            for ref in refs:
                path=case_dir/"cases"/(ref["reference_session"]+".json")
                if not path.exists(): rows.append({"cik":profile["cik"],"sector_id":profile["sector_id"],"phase":ref["phase"],"reference_session":ref["reference_session"],"status":"unavailable","reason":"MISSING_SOURCE_CASE"}); continue
                source_hashes[str(path)]=digest(path); rows.append(compact(read(path),profile,ref,bars,calendar))
        atomic_json(target,{"cik":profile["cik"],"source_hashes":source_hashes,"rows":rows}); processed+=1
    ready=[destination/"issuers"/(p["cik"]+".json") for p in profiles]
    if not all(p.exists() for p in ready): return {"status":"running_coefficient_audit","completed_issuers":sum(p.exists() for p in ready),"expected_issuers":len(profiles)}
    final=destination/"AUDIT.json"
    if not final.exists():
        all_rows=[]
        for path in ready:
            value=read(path)
            if any(digest(name)!=sha for name,sha in value["source_hashes"].items()): raise ValueError("AUDIT_SOURCE_CHANGED_AFTER_CHECKPOINT")
            all_rows.extend(value["rows"])
        result={"status":"retrospective_price_subset_audit_not_validation","completed_at":now(),"source_job":str(Path(job_path).resolve()),"fingerprint":fingerprint,"phases":{},"current_news_or_financial_backfill":False,"final_cases_read":0,"unseen_issuers_read":0,"coefficients_fitted":False,"released_score":None,"point_in_time_verified":False}
        for phase in ("development","validation"):
            result["phases"][phase]=phase_summary([r for r in all_rows if r["phase"]==phase],[r for r in refs if r["phase"]==phase])
        atomic_json(final,result)
        (destination/"REPORT.ko.md").write_text("# 계수 관련성 비교\n\n현재 23개 변수 중 과거에 재생 가능한 가격 9개 변수만 평가했습니다. 같은 날짜의 종목 순위와 이후 5거래일 비용 후 보유 수익의 관련성을 기존 계수·동일 계수로 비교합니다.\n\n계수 학습·선정이나 수익성 승인이 아니며 현재 뉴스·재무, 마지막 시험·미관측 기업을 사용하지 않았습니다. 수치와 기간별 부족은 AUDIT.json에 보존합니다.\n",encoding="utf-8")
    return {"status":"coefficient_audit_complete","completed_issuers":len(profiles),"result_path":str(final)}
