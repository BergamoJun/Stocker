"""Paired fixed-candidate rank diagnostic reusing the completed source replays."""
from collections import Counter,defaultdict
from decimal import Decimal,localcontext
import json
from pathlib import Path
import random
import time
from research import historical_panel,panel_prices
from research.local_files import atomic_json
from research.panel_transport import disk_name
from research.rights import require_entitlement,price_uses
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest,stable_hash
from . import coefficient_audit as previous,turnover_features as features

def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def signed_record(value): return {**value,'record_sha256':stable_hash(value)}
def checked_record(path):
    value=read(path)
    if stable_hash({k:v for k,v in value.items() if k!='record_sha256'})!=value.get('record_sha256'): raise ValueError('TURNOVER_CHECKPOINT_CHANGED')
    return value

def source_ready(source_job,prior_followup):
    job,state,folder=previous.source_status(source_job)
    if state['status'] not in ('complete','complete_with_gaps') or state['stage']!='done':
        if state['status'] in ('withheld','exhausted'): return None,'blocked_source'
        return None,'waiting_for_full_panel'
    from .followup_job import check
    check(prior_followup)
    follow=read(Path(prior_followup).with_name('state.json'))
    if follow.get('coefficients',{}).get('status')!='coefficient_audit_complete':
        if follow.get('status') in ('stopped','stopped_integrity','complete_with_blocks'): return None,'blocked_prior_replay'
        return None,'waiting_for_prior_replay'
    old=Path(prior_followup).parent/'coefficient_audit'
    if Path(follow['coefficients']['result_path']).resolve()!=(old/'AUDIT.json').resolve(): raise ValueError('PRIOR_AUDIT_PATH_CHANGED')
    # Source-use rights precede all parsing of licensed prices or derived rows.
    entitlement=read(folder/'price_entitlement.json'); require_entitlement(entitlement,price_uses('local_research'),now())
    if digest(folder/'cohort.json')!=state['cohort_sha256']: raise ValueError('TURNOVER_COHORT_CHANGED')
    cohort=read(folder/'cohort.json'); profiles=previous.selected_profiles(cohort)
    expected={'job_sha256':digest(source_job),'cohort_sha256':digest(folder/'cohort.json'),'method_sha256':digest(Path(previous.__file__)),'policy_sha256':digest(Path(previous.__file__).with_name('followup_policy.json')),'weighted_policy_sha256':digest(Path(previous.__file__).with_name('weighted_policy.json'))}
    old_manifest=read(old/'manifest.json'); summary=read(old/'AUDIT.json')
    if old_manifest!=expected or summary['fingerprint']!=expected or summary['coefficients_fitted'] or summary['final_cases_read'] or summary['unseen_issuers_read']: raise ValueError('PRIOR_REPLAY_METHOD_CHANGED')
    allowed={p['cik']+'.json' for p in profiles}
    if any(p.name not in allowed for p in (old/'issuers').glob('*.json')): raise ValueError('TURNOVER_UNEXPECTED_OR_FINAL_CHECKPOINT')
    checkpoints={p['cik']:digest(old/'issuers'/(p['cik']+'.json')) for p in profiles}
    fingerprint={'source_job_sha256':digest(source_job),'cohort_sha256':digest(folder/'cohort.json'),'prior_manifest_sha256':digest(old/'manifest.json'),'prior_result_sha256':digest(old/'AUDIT.json'),
        'prior_issuer_sha256':checkpoints,'policy_sha256':digest(Path(features.__file__).with_name('turnover_policy.json')),'feature_code_sha256':digest(Path(features.__file__)),'audit_code_sha256':digest(Path(__file__))}
    return {'job':job,'state':state,'folder':folder,'old':old,'profiles':profiles,'entitlement':entitlement,'fingerprint':fingerprint},None

def missing(profile,ref,reason):
    return {'cik':profile['cik'],'sector_id':profile['sector_id'],'phase':ref['phase'],'reference_session':ref['reference_session'],'status':'unavailable','reason':reason,
        'signals':{k:None for k in features.policy()['candidates']},'max_upside_5_pct':None,'hold_net_return_pct':None}

def paired_case(case,prior,profile,ref,bars,calendar,*,illustrative=False):
    rebuilt=previous.compact(case,profile,ref,bars,calendar,replay=False,illustrative=illustrative)
    if rebuilt!=prior: raise ValueError('PRIOR_FACTOR_CHECKPOINT_RECONSTRUCTION_CHANGED')
    result=missing(profile,ref,prior['reason'])
    if prior['status']!='ready': return result
    f=features.calculate(bars,ref['reference_session'],calendar=calendar,historical=True)
    if f['status']!='available': return missing(profile,ref,f['reason_code'] or 'TURNOVER_INCOMPLETE')
    index=next(i for i,b in enumerate(bars) if b.session==ref['reference_session'])
    future=[b for b in bars if b.session in ref['label_sessions']]
    if [b.session for b in future]!=ref['label_sessions']: raise ValueError('TURNOVER_LABEL_SESSIONS_MISSING')
    original=previous.factors(case,bars[:index+1])['volume_confirmation']['value']
    signals=features.replacement_signals(prior['weighted_signed_signal'],original,f)
    with localcontext() as context:
        context.prec=60; peak=(max(b.high for b in future)/bars[index].close-1)*100
    return {**result,'status':'ready','reason':None,'signals':signals,'max_upside_5_pct':features.decimal_text(peak),'hold_net_return_pct':prior['hold_net_return_pct']}

def summarize(rows,refs,settings=None):
    s=settings or features.policy()['evaluation']; models=features.policy()['candidates']; by=defaultdict(list)
    expected={(r['cik'],r['reference_session']) for r in rows}
    if len(expected)!=len(rows): raise ValueError('TURNOVER_DUPLICATE_PAIR')
    if any(r['reference_session'] not in {x['reference_session'] for x in refs} for r in rows): raise ValueError('TURNOVER_UNEXPECTED_REFERENCE')
    for row in rows:
        if row['status']=='ready':
            if any(row['signals'].get(k) is None for k in models) or any(row[k] is None for k in ('max_upside_5_pct','hold_net_return_pct')): raise ValueError('TURNOVER_INCOMPLETE_COMPARISON')
            by[row['reference_session']].append(row)
    endpoints={}
    for outcome in ('max_upside_5_pct','hold_net_return_pct'):
        daily=[]
        for ref in refs:
            group=by[ref['reference_session']]; record={'reference_session':ref['reference_session'],'issuers':len(group),'status':'insufficient_issuers','correlations':{},'differences':{}}
            if len(group)>=s['minimum_issuers_per_date']:
                y=[features.number(r[outcome]) for r in group]
                correlations={k:previous.spearman([features.number(r['signals'][k]) for r in group],y) for k in models}
                if all(v is not None for v in correlations.values()): record.update(status='available',correlations=correlations,differences={k:correlations[k]-correlations[models[0]] for k in models[1:]})
                else: record['status']='constant_signal_or_outcome'
            daily.append(record)
        available=[d for d in daily if d['status']=='available']; comparisons={}
        for model in models[1:]:
            intervals=[]
            for block in [s['primary_block_length']]+s['sensitivity_block_lengths']:
                info={'block_length':block,'nominal_blocks':len(available)//block,'status':'withheld_insufficient_complete_dates_or_time_blocks','difference_interval':None}
                if len(available)==len(refs) and len(available)>=s['minimum_reference_dates'] and len(available)//block>=s['minimum_nonoverlapping_blocks']:
                    x=[d['differences'][model] for d in available]; rng=random.Random(s['seed']+block); means=[]
                    for _ in range(s['bootstrap_replications']):
                        sample=[]
                        while len(sample)<len(x):
                            start=rng.randrange(len(x)-block+1); sample.extend(x[start:start+block])
                        means.append(sum(sample[:len(x)])/len(x))
                    means.sort(); info.update(status='exploratory_pointwise_not_multiple_test_adjusted',difference_interval=[means[int(.025*(len(means)-1))],means[int(.975*(len(means)-1))]])
                intervals.append(info)
            comparisons[model]={'mean_rank_correlation_difference':sum(d['differences'][model] for d in available)/len(available) if available else None,'intervals':intervals}
        endpoints[outcome]={'daily':daily,'available_reference_dates':len(available),'mean_rank_correlations':{k:sum(d['correlations'][k] for d in available)/len(available) if available else None for k in models},'comparisons':comparisons}
    usable=sum(r['status']=='ready' for r in rows)
    return {'expected_slots':len(rows),'available_slots':usable,'missing_slots':len(rows)-usable,'missing_reasons':dict(Counter(r['reason'] for r in rows if r['reason'])),'endpoints':endpoints,'independent_sample_count':None,'winner_selected':False}

def report(value):
    lines=['# 거래대금 근사 후보의 과거 비교','',
        '기존 가격 9개 변수 중 거래량 계수 2만 교체했습니다. 세 후보 모두 같은 자료·같은 계수 합을 사용합니다. 현재 뉴스와 당시 없던 재무 자료는 넣지 않았습니다.','',
        '첫 비교값은 이후 5거래일 최고가의 기준 종가 대비 상승폭입니다. 이는 매도 가능한 확정 수익이 아닙니다. 둘째는 매수·매도 각각 0.25% 비용을 반영한 기존 5일 보유 수익률입니다.','',
        '순위 상관은 -1~1 범위이며 양수이면 높은 후보 점수가 더 큰 관측 결과와 함께 나타났다는 뜻입니다. 적중률·승률·인과효과를 뜻하지 않습니다.','']
    names={'original_volume':'원래 거래량','relative_notional':'상대 거래대금 근사','persistent_notional':'지속성 결합'}
    for phase,data in value['phases'].items():
        lines += [f"## {'개발' if phase=='development' else '검증'} 구간",'',f"기대 사례 {data['expected_slots']}건 중 공통 비교 가능 {data['available_slots']}건, 부족 {data['missing_slots']}건.",'']
        for endpoint,result in data['endpoints'].items():
            lines += [f"비교 대상: {'5일 최고가 상승 기회' if endpoint=='max_upside_5_pct' else '비용 후 5일 보유 수익'}",'','| 후보 | 날짜별 순위 상관의 평균 |','|---|---:|']
            lines += [f"| {names[k]} | {v} |" for k,v in result['mean_rank_correlations'].items()]
            for model,c in result['comparisons'].items():
                primary=c['intervals'][0]; lines += ['',f"{names[model]}와 원래 변수의 상관 차이: {c['mean_rank_correlation_difference']}. 날짜 묶음 구간: {primary['difference_interval']} · 상태: {primary['status']}."]
            lines += ['']
    lines += ['구간은 여러 후보·비교값에 대한 탐색적 개별 구간입니다. 종목×날짜 행이나 재표집 횟수는 독립 시장 표본 수가 아닙니다. 기업·기간 잠금과 원래 가격 예측은 유지하며 후보 자동 채택·계수 학습·뉴스 가점·정식 추천도는 활성화하지 않습니다.','']
    return '\n'.join(lines)

def step(source_job,prior_followup,output,*,max_issuers=100,finish_before=None):
    context,waiting=source_ready(source_job,prior_followup)
    if waiting: return {'status':waiting,'processed_issuers':0}
    out=Path(output).resolve()
    if not out.is_relative_to((ROOT/'reports/private').resolve()): raise ValueError('PRIVATE_TURNOVER_AUDIT_REQUIRED')
    out.mkdir(parents=True,exist_ok=True); fingerprint=context['fingerprint']; manifest=out/'manifest.json'
    if manifest.exists() and read(manifest)!=fingerprint: raise ValueError('TURNOVER_AUDIT_DEPENDENCY_CHANGED')
    if not manifest.exists(): atomic_json(manifest,fingerprint)
    refs=historical_panel.references(); count=0
    allowed_outputs={p['cik']+'.json' for p in context['profiles']}
    if any(p.name not in allowed_outputs for p in (out/'issuers').glob('*.json')): raise ValueError('TURNOVER_UNEXPECTED_OUTPUT_ISSUER')
    for profile in context['profiles']:
        target=out/'issuers'/(profile['cik']+'.json')
        if target.exists():
            record=checked_record(target)
            if record['fingerprint_sha256']!=stable_hash(fingerprint): raise ValueError('TURNOVER_CHECKPOINT_METHOD_CHANGED')
            if record['cik']!=profile['cik'] or any(not Path(name).resolve().is_relative_to(context['folder'].resolve()) or digest(name)!=sha for name,sha in record['source_hashes'].items()): raise ValueError('TURNOVER_SOURCE_CHANGED_AFTER_CHECKPOINT')
            continue
        if count>=max_issuers or finish_before is not None and time.monotonic()>=finish_before: break
        old_path=context['old']/'issuers'/(profile['cik']+'.json')
        if digest(old_path)!=fingerprint['prior_issuer_sha256'][profile['cik']]: raise ValueError('TURNOVER_PRIOR_CHECKPOINT_CHANGED')
        prior=read(old_path)
        if prior['cik']!=profile['cik'] or len(prior['rows'])!=len(refs): raise ValueError('PRIOR_ISSUER_OR_COHORT_CHANGED')
        for name,sha in prior['source_hashes'].items():
            if not Path(name).resolve().is_relative_to(context['folder'].resolve()) or digest(name)!=sha: raise ValueError('TURNOVER_SOURCE_CHANGED')
        ticker=profile['symbol']; case_dir=context['folder']/'evaluation'/disk_name(ticker)
        expected={r['reference_session']+'.json' for r in refs}
        if case_dir.exists() and any(p.name not in expected for p in (case_dir/'cases').glob('*.json')): raise ValueError('TURNOVER_UNEXPECTED_OR_FINAL_CASE')
        rows=[]; loaded=None
        for ref,old_row in zip(refs,prior['rows']):
            if old_row['phase']!=ref['phase'] or old_row['reference_session']!=ref['reference_session'] or old_row['cik']!=profile['cik']: raise ValueError('TURNOVER_PRIOR_ROW_ORDER_CHANGED')
            if old_row['status']!='ready': rows.append(missing(profile,ref,old_row['reason'])); continue
            if loaded is None:
                price_path=context['folder']/'prices'/disk_name(ticker)/'prices.json'
                if str(price_path) not in prior['source_hashes']: raise ValueError('TURNOVER_PRICE_NOT_REPLAY_BOUND')
                _,bars,calendar=panel_prices.load(price_path,profile,context['entitlement'],now()); loaded=(bars,calendar)
            path=case_dir/'cases'/(ref['reference_session']+'.json')
            if str(path) not in prior['source_hashes']: raise ValueError('TURNOVER_CASE_NOT_REPLAY_BOUND')
            rows.append(paired_case(read(path),old_row,profile,ref,*loaded))
        source_hashes=dict(prior['source_hashes'])
        if loaded is not None: source_hashes[str(price_path.with_name('raw_response.json'))]=digest(price_path.with_name('raw_response.json'))
        atomic_json(target,signed_record({'cik':profile['cik'],'fingerprint_sha256':stable_hash(fingerprint),'source_hashes':source_hashes,'rows':rows})); count+=1
    expected_paths=[out/'issuers'/(p['cik']+'.json') for p in context['profiles']]
    completed=sum(p.exists() for p in expected_paths)
    if completed<len(expected_paths): return {'status':'running_turnover_audit','processed_issuers':completed,'expected_issuers':len(expected_paths)}
    if not (out/'AUDIT.json').exists():
        rows=[]
        for path in expected_paths:
            record=checked_record(path)
            if any(not Path(name).resolve().is_relative_to(context['folder'].resolve()) or digest(name)!=sha for name,sha in record['source_hashes'].items()): raise ValueError('TURNOVER_SOURCE_CHANGED_AFTER_CHECKPOINT')
            rows.extend(record['rows'])
        result={'status':'retrospective_rank_diagnostic_not_forecast_validation','completed_at':now(),'source_job':str(Path(source_job).resolve()),'prior_followup':str(Path(prior_followup).resolve()),'fingerprint':fingerprint,
            'phases':{phase:summarize([r for r in rows if r['phase']==phase],[r for r in refs if r['phase']==phase]) for phase in ('development','validation')},
            'final_cases_read':0,'unseen_issuers_read':0,'coefficients_fitted':False,'winner_selected':False,'news_backfill':False,'released_score':None,'point_in_time_verified':False}
        result=signed_record(result); atomic_json(out/'AUDIT.json',result); (out/'REPORT.ko.md').write_text(report(result),encoding='utf-8')
        atomic_json(out/'FILES.json',{'AUDIT.json':digest(out/'AUDIT.json'),'REPORT.ko.md':digest(out/'REPORT.ko.md')})
    value=checked_record(out/'AUDIT.json')
    files=read(out/'FILES.json')
    if value['fingerprint']!=fingerprint or set(files)!={'AUDIT.json','REPORT.ko.md'} or any(digest(out/name)!=sha for name,sha in files.items()) or (out/'REPORT.ko.md').read_text(encoding='utf-8')!=report(value): raise ValueError('TURNOVER_FINAL_OUTPUT_CHANGED')
    return {'status':'turnover_audit_complete','processed_issuers':completed,'expected_issuers':len(expected_paths),'result_path':str(out/'AUDIT.json')}
