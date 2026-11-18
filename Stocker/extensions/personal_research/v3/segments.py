"""Business-segment axis only. Geography/product dimensions never become segments."""
from datetime import date
from decimal import Decimal
from .coverage import documents, reference
from extensions import financial_fill

REVENUE={'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax','us-gaap:Revenues','us-gaap:SalesRevenueNet'}


def member(dimensions):
    business=[d[2] for d in dimensions if d[0]=='explicitmember' and d[1]=='us-gaap:StatementBusinessSegmentsAxis']
    others=[d for d in dimensions if d[1]!='us-gaap:StatementBusinessSegmentsAxis']
    if len(business)!=1 or any(tuple(d)!=('explicitmember','srt:ConsolidationItemsAxis','us-gaap:OperatingSegmentsMember') for d in others):return None
    return business[0]


def compatible(x):
    return x['same_entity'] and not x['hidden'] and x['status']=='parsed_numeric_evidence_only' and x['unit']=='USD' and x['start'] and x['end']


def extract(snapshot,at):
    groups=[];refs={}
    for f,sid,s,raw,parsed,dims in documents(snapshot,at):
        eligible=[x for x in parsed if compatible(x)]
        current=[x for x in eligible if x['end']==f['report_date'] and member(dims.get(x['context_ref'],[])) and x['tag'] in REVENUE]
        starts=sorted({x['start'] for x in current})
        if not starts:continue
        # Annual or longest disclosed YTD; never mix the quarter and YTD columns.
        start=starts[0];end=f['report_date'];names=sorted({member(dims[x['context_ref']]) for x in current if x['start']==start})
        entries=[]
        for name in names:
            rows=[x for x in eligible if member(dims.get(x['context_ref'],[]))==name]
            rev=financial_fill.unique([x for x in rows if x['tag'] in REVENUE and x['start']==start and x['end']==end])[0]
            if not rev:continue
            op=financial_fill.unique([x for x in rows if x['tag']=='us-gaap:OperatingIncomeLoss' and x['start']==start and x['end']==end])[0]
            prior=[]
            for x in rows:
                if x['tag']!=rev['tag']:continue
                try:shifted=date.fromisoformat(end).replace(year=int(end[:4])-1)
                except ValueError:continue
                annual=f['form']=='10-K';span=(date.fromisoformat(x['end'])-date.fromisoformat(x['start'])).days
                if annual and abs((date.fromisoformat(x['end'])-shifted).days)<=7 and 330<=span<=370:prior.append(x)
                elif not annual and x['end']==shifted.isoformat() and x['start']==date.fromisoformat(start).replace(year=int(start[:4])-1).isoformat():prior.append(x)
            prev=financial_fill.unique(prior)[0];source_refs=[]
            for x in [rev,op,prev]:
                if x:
                    ref,meta=reference(f,sid,s,x);refs[ref]=meta;source_refs.append(ref)
            value=Decimal(rev['value']);old=Decimal(prev['value']) if prev else None
            entries.append({'id':'segment:'+f['accession']+':'+name,'segment_name':name.split(':')[-1].removesuffix('Member'),'taxonomy_member':name,
                'segment_revenue':rev['value'],'segment_operating_income':op['value'] if op else None,
                'segment_profit_margin':str(Decimal(op['value'])/value*100) if op and value>0 else None,
                'prior_period_segment_revenue':prev['value'] if prev else None,
                'prior_period':{'start':prev['start'],'end':prev['end']} if prev else None,
                'segment_growth_rate':str((value/old-1)*100) if old and old>0 else None,
                'segment_reporting_period':{'start':start,'end':end,'basis':'annual' if f['form']=='10-K' else 'cumulative_ytd'},
                'source_refs':source_refs,'evidence_kind':'OBSERVED','derived_fields':['segment_profit_margin','segment_growth_rate'],
                'limitations':['同一 공시의 같은 taxonomy member만 비교합니다. 조직 개편 전후를 자동 연결하지 않습니다.',
                    '사업부 영업손익은 연결 영업손익과 다를 수 있습니다. 회계연도 길이가 다르면 성장률 해석에 유의합니다.']})
        total=financial_fill.unique([x for x in eligible if not dims.get(x['context_ref']) and x['tag'] in REVENUE and x['start']==start and x['end']==end])[0]
        sum_revenue=sum((Decimal(x['segment_revenue']) for x in entries),Decimal(0))
        target=Decimal(total['value']) if total else None
        total_refs=[]
        if total:
            ref,meta=reference(f,sid,s,total);refs[ref]=meta;total_refs=[ref]
        groups.append({'accession':f['accession'],'form':f['form'],'period':{'start':start,'end':end},'segments':entries,
            'consolidated_total':total['value'] if total else None,'consolidated_total_refs':total_refs,
            'reconciliation_status':'RECONCILED' if entries and target is not None and sum_revenue==target else 'RECONCILIATION_REQUIRED',
            'unreconciled_difference':str(target-sum_revenue) if target is not None else None,
            'dimension_policy':'one business-segment axis; optional OperatingSegments consolidation member only'})
    return sorted(groups,key=lambda x:(x['period']['end'],x['period']['start']),reverse=True),refs
