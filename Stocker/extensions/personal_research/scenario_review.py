"""Review P4 assumptions without editing or certifying their forecasts."""
from decimal import Decimal,localcontext
from . import scenarios

VERSION='scenario-plausibility-review-v1'


def build(value,pack):
    result={'version':VERSION,'status':'PARTIAL','scenarios':[],'guidance_status':'UNKNOWN','historical_references':value.get('memo_historical_evidence',[]),
        'source_conflicts':pack['conflicts'],'economic_tensions':[],'independent_plausibility_accuracy':'NOT_MEASURED',
        'limitations':['Labels are evidence support, not probabilities. P4 calculations and assumptions are unchanged.']}
    if value['market_snapshot'].get('security_type')=='ETF':result['status']='NOT_APPLICABLE';return result
    if not value.get('scenario_results'):result['status']='SCENARIO_REVIEW_UNAVAILABLE';return result
    ref=value['scenario_reference'];r=Decimal(ref['reference_revenue'])
    with localcontext() as c:
        c.prec=60
        observed={'net_margin':Decimal(ref['reference_net_income'])/r if ref['reference_net_income'] is not None else None,
            'operating_margin':Decimal(ref['reference_operating_income'])/r if ref['reference_operating_income'] is not None else None,
            'fcf_margin':Decimal(ref['reference_fcf'])/r if ref['reference_fcf'] is not None else None,
            'exit_pe':Decimal(ref['current_pe']) if ref['current_pe'] is not None else None,
            'exit_ps':Decimal(ref['current_ps']) if ref['current_ps'] is not None else None,'growth':None}
        segments=value.get('business_segments',[])
        if segments and segments[0]['segments'] and all(s['prior_period_segment_revenue'] is not None for s in segments[0]['segments']):
            prior=sum((Decimal(s['prior_period_segment_revenue']) for s in segments[0]['segments']),Decimal(0))
            if prior>0:observed['growth']=sum((Decimal(s['segment_revenue']) for s in segments[0]['segments']),Decimal(0))/prior-1
        if observed['net_margin'] is not None and observed['fcf_margin'] is not None and observed['net_margin']>observed['fcf_margin']:
            result['economic_tensions'].append({'kind':'EARNINGS_VS_CASH_CONVERSION','input_refs':ref['input_refs'],
                'statement':'회계 이익률과 현금 전환율의 차이는 원문 충돌이 아니라 투자비·운전자본 등을 확인할 경제적 점검 항목입니다.'})
        for assumption,row in zip(value['scenario_assumptions'],value['scenario_results']):
            fields=[];conditions=[]
            canonical=scenarios.calculate(ref,assumption)
            coherence='CONDITIONAL' if canonical==row else 'CONFLICTING'
            for name,f in assumption['fields'].items():
                amount=Decimal(f['assumption_value']);anchor=observed.get(name);kind=f['assumption_type']
                support='USER_ASSUMPTION' if kind=='USER_ASSUMPTION' else 'ILLUSTRATIVE_HEURISTIC'
                label='INDETERMINATE' if kind=='USER_ASSUMPTION' else 'EXTRAPOLATED' if anchor is not None and amount==anchor else 'STRESS_TEST'
                rationale='공시 관측값과 연구 충격을 구분합니다. 향후 지속은 추가 가정입니다.'
                if name.startswith('exit_') and anchor is not None and amount==anchor:
                    support='OBSERVED_ANCHOR';rationale='현재 관측 배수를 종료 시점에 유지한다는 가정이며 회사 가이던스가 아닙니다.'
                if name=='growth':
                    rationale='최근 공시의 사업부/매출 비교를 연간 모형 단계로 이어가는 것은 외삽이며 미래 성장을 입증하지 않습니다.'
                    if anchor is None:label='WEAKLY_SUPPORTED'
                if value['ticker']=='MU' and assumption['scenario_name']=='base' and name=='net_margin':
                    support='ILLUSTRATIVE_HEURISTIC';label='WEAKLY_SUPPORTED'
                    rationale='최근 마진의 절반을 쓰는 연구용 정상화입니다. 비교 연간 공시가 있어도 이를 역사 평균이나 시장 컨센서스로 해석하지 않습니다.'
                if kind=='SOURCE_BACKED_GUIDANCE':support='UNSUPPORTED';label='INDETERMINATE';rationale='직접 연결된 발표일·적용기간·가이던스 근거를 확인하지 못했습니다.'
                history=result['historical_references'] if name=='net_margin' else []
                history_values=[Decimal(h['value']) for h in history]
                historical_comparison={'status':'INSUFFICIENT_HISTORY','periods':[]}
                if history_values:
                    historical_comparison={'status':'WITHIN_OBSERVED_COMPARATIVE_RANGE' if min(history_values)<=amount<=max(history_values) else 'OUTSIDE_OBSERVED_COMPARATIVE_RANGE',
                        'minimum':str(min(history_values)),'maximum':str(max(history_values)),
                        'periods':[{'period':h['period'],'days':h['period_days'],'source_refs':h['source_refs']} for h in history],
                        'limitation':'Current-filing comparative range, mixed52/53-week durations; not a long-run average or future probability.'}
                fields.append({'assumption_name':name,'original_assumption':f,'observed_reference':str(anchor) if anchor is not None else None,
                    'historical_comparison':historical_comparison,
                    'comparison_period':ref['reference_period'],'reference_period_classification':ref['reference_period_classification'],
                    'support_type':support,'plausibility_status':label,'rationale':rationale,
                    'limitation':'기간·업황·비용·금융구조 변화 및 미래 가용성은 미확인입니다. 관측 범위 안이어도 미래 확률이 아닙니다.',
                    'suggested_monitoring_metric':'매출/사업부 성장과 같은 기간의 CAPEX·OCF·이익률 및 실제 시장 배수'})
            future=assumption['fields'].get('fcf_margin');growth=assumption['fields'].get('growth')
            if future and growth and observed['fcf_margin'] is not None and Decimal(growth['assumption_value'])>0 and Decimal(future['assumption_value'])>observed['fcf_margin']:
                conditions.append('성장과 FCF margin 동시 개선에는 투자 효율·운전자본·현금 회수 조건의 개선을 추가 확인해야 합니다.')
            if ref.get('reference_period_days')==371:conditions.append('53주/371일 실적은 정확한 달력12개월 TTM이나52주 실적과 동일하지 않습니다.')
            conditions.append('미래 마진과 종료 배수의 지속 및 희석·자본 조달·투자비 회수는 별도 근거가 필요합니다.')
            result['scenarios'].append({'scenario_id':'S_'+assumption['scenario_name'],'name':assumption['scenario_name'],
                'coherence':coherence,'assumptions':fields,'additional_conditions':conditions,
                'calculation_status':'MATCHES_UNCHANGED_P4' if canonical==row else 'P4_RESULT_CONFLICT','probability':None})
    return result
