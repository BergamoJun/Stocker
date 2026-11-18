"""Bounded extractive SEC analysis. No model, web search or ungrounded industry facts."""
from html.parser import HTMLParser
from datetime import date
import re
from .coverage import documents

TOPICS={
 'cloud':('클라우드·AI 서비스',r'\bcloud\b|\bAzure\b|artificial intelligence'),
 'software':('소프트웨어·구독',r'\bMicrosoft 365\b|\bsubscription\b|\bsoftware\b'),
 'memory':('메모리·DRAM/NAND/HBM',r'\bDRAM\b|\bNAND\b|\bHBM\b|high.bandwidth memory'),
 'power':('연료전지·전력 공급',r'fuel cell|Energy Server|solid oxide'),
 'energy':('원유·가스·정제·화학',r'crude oil|natural gas|\brefining\b|chemical products'),
 'investment':('생산능력·투자 부담',r'capital expenditures|capital investments|manufacturing capacity'),
 'competition':('공시된 경쟁 환경',r'\bcompetitors\b|\bcompetition\b|\bcompetitive\b'),
 'supply':('공급망·생산 제약',r'supply chain|supply constraints|manufacturing disruption'),
 'cycle':('수요·가격 사이클',r'cyclical|fluctuations in|selling prices|commodity prices'),
 'customers':('고객·판매 구조',r'customers|customer concentration'),
 'regulation':('규제·정책 위험',r'export controls|environmental regulations|government regulations')}


class Blocks(HTMLParser):
    """Leaf block text with exact original HTML character offsets, not invented sentences."""
    def __init__(self,text):
        super().__init__(convert_charrefs=True);self.raw=text;self.frames=[];self.rows=[];self.skip=0;self.tags=[]
        self.lines=[0]+[m.end() for m in re.finditer('\n',text)]
    def charpos(self):
        line,col=self.getpos();return self.lines[line-1]+col
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        hidden=tag in {'script','style','ix:header','ix:hidden'} or 'display:none' in attrs.get('style','').replace(' ','').lower()
        if tag not in {'br','hr','img','meta','link','input','wbr'}:self.tags.append((tag,hidden))
        self.skip=sum(h for _,h in self.tags)
        if tag in {'p','div','h1','h2','h3','h4','td','li'}:
            for f in self.frames:f['child']=True
            self.frames.append({'tag':tag,'start':self.charpos(),'parts':[],'child':False,'hidden':bool(self.skip),'id':attrs.get('id')})
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs);self.handle_endtag(tag)
    def handle_data(self,data):
        if not self.skip:
            for f in self.frames:f['parts'].append(data)
    def handle_endtag(self,tag):
        if self.frames and self.frames[-1]['tag']==tag:
            f=self.frames.pop();text=' '.join(''.join(f['parts']).split())
            if not f['hidden'] and not f['child'] and text:
                end=self.raw.find('>',self.charpos())+1
                self.rows.append({'text':text,'char_start':f['start'],'char_end':end,'dom_id':f['id']})
        matches=[i for i,(t,h) in enumerate(self.tags) if t==tag]
        if matches:self.tags=self.tags[:matches[-1]]
        self.skip=sum(h for _,h in self.tags)


def paragraphs(raw,form):
    text=raw.decode('utf-8-sig');parser=Blocks(text);parser.feed(text);parser.close()
    section='UNCLASSIFIED';out=[]
    for n,p in enumerate(sorted(parser.rows,key=lambda p:p['char_start'])):
        t=p['text'];heading=re.match(r'^item\s+(\d+[a-z]?)\s*[.\-:\s]*(.*)$',t,re.I)
        if heading and len(t)<240:
            number=heading[1].upper();tail=heading[2].lower()
            section={'1':'BUSINESS' if form=='10-K' else 'FINANCIAL_STATEMENTS','1A':'RISK_FACTORS',
                '7':'MD&A' if form=='10-K' else 'OTHER','2':'MD&A' if form=='10-Q' else 'OTHER'}.get(number,'OTHER')
        out.append({**p,'paragraph_ordinal':n,'section':section})
    return out


def extract(snapshot,at):
    facts=[];refs={};inventory=[];seen=set()
    for f,sid,s,raw,parsed,dims in documents(snapshot,at):
        blocks=paragraphs(raw,f['form']);counts={k:sum(p['section']==k for p in blocks) for k in ['BUSINESS','RISK_FACTORS','MD&A']}
        inventory.append({'accession':f['accession'],'form':f['form'],'sections':counts,'segment_note_blocks':sum(bool(re.search(r'\bsegment',p['text'],re.I)) for p in blocks)})
        for topic,(label,pattern) in TOPICS.items():
            for section in ['BUSINESS','RISK_FACTORS','MD&A']:
                candidates=[p for p in blocks if p['section']==section and 70<=len(p['text'])<=1800 and re.search(pattern,p['text'],re.I)]
                if not candidates:continue
                # Rank actual topic occurrences, then preserve document order. At most one
                # paragraph per topic/section/document; no free-form claim generation.
                p=max(candidates,key=lambda p:(len(re.findall(pattern,p['text'],re.I)),-p['paragraph_ordinal']))
                key=(sid,p['char_start'])
                if key in seen:continue
                seen.add(key);ref=sid+':text:'+str(p['paragraph_ordinal'])
                refs[ref]={'url':s['source_url'],'raw_path':s['raw_path'],'raw_sha256':s['raw_sha256'],'available_at':s['available_at'],
                    'published_at':f.get('accepted_at'),'filed_date':f['filed_date'],'accession':f['accession'],
                    'locator':{'section':section,'paragraph_ordinal':p['paragraph_ordinal'],'char_start':p['char_start'],'char_end':p['char_end'],'dom_id':p['dom_id']}}
                facts.append({'id':ref,'issuer':snapshot['cik'],'ticker':snapshot['identity']['symbols'][0],
                    'source_type':'SEC_'+f['form'],'document_date':f['filed_date'],'available_at':s['available_at'],
                    'statement':label+'에 대한 회사 공시 설명','source_text':p['text'],'kind':'SOURCE_FACT','evidence_kind':'OBSERVED',
                    'topic':topic,'section':section,'source_refs':[ref],
                    'limitations':['회사의 설명·전망·위험 공시이며 독립적으로 입증된 경쟁 우위나 산업 전망이 아닙니다.']})
    return facts,refs,inventory


def enrich(value,snapshot,at):
    empty={'business_profile':[],'growth_drivers':[],'competitive_factors':[],'industry_context':[],
        'recent_events':[],'investment_theses':[],'counter_theses':[],'company_risks':[],'business_segments':[],
        'business_source_inventory':[],'analysis_limitations':['외부 산업 성장률·시장점유율·기사 본문은 미수집이며 독립 의미 정확도는 NOT_MEASURED입니다.']}
    value.update(empty)
    if not snapshot or value['market_snapshot'].get('security_type')=='ETF':return
    from . import segments
    facts,refs,inventory=extract(snapshot,at);groups,srefs=segments.extract(snapshot,at)
    value['source_references'].update(refs);value['source_references'].update(srefs)
    value['business_profile']=facts;value['business_segments']=groups;value['business_source_inventory']=inventory
    for field,topics,section in [('growth_drivers',{'cloud','software','memory','power','energy','investment'},None),
        ('company_risks',set(TOPICS),'RISK_FACTORS'),('competitive_factors',{'competition'},None),
        ('industry_context',{'cycle','regulation','supply'},None)]:
        value[field]=[x['id'] for x in facts if x['topic'] in topics and (not section or x['section']==section)]
    if not value['company_risks']:
        value['company_risks']=[x['id'] for x in facts if x['topic'] in {'cycle','supply','regulation'} and
            re.search(r'risk|adverse|volatil|fluctuat|uncertain',x['source_text'],re.I)]
    # Theses require both an actual business paragraph and observed financial context.
    finance=[x for x in value['facts'] if x['role']=='current' and x['value'] is not None]
    operational=next((x for x in facts if x['topic'] in {'cloud','software','memory','power','energy'} and x['section']!='RISK_FACTORS'),None)
    risk=next((x for x in facts if x['section']=='RISK_FACTORS'),None)
    cash=next((x for x in finance if x['name']=='operating_cash_flow'),None)
    capex=next((x for x in finance if x['name']=='capital_expenditure'),None)
    if operational and cash:
        from decimal import Decimal
        context=[cash['id']]+([capex['id']] if capex else [])
        supporting=[operational['id']]+context
        sources=operational['source_refs']+cash['source_refs']+(capex['source_refs'] if capex else [])
        segment=max(groups[0]['segments'],key=lambda x:Decimal(x['segment_revenue'])) if groups and groups[0]['segments'] else None
        if segment:
            supporting.append(segment['id']);sources+=segment['source_refs']
            trend=(f'전년 공시 대비 매출 변화 {Decimal(segment["segment_growth_rate"]):.2f}%' if segment['segment_growth_rate'] is not None else '전년 비교값 미확인')
            lead=f'{segment["segment_name"]} 사업부의 {segment["segment_reporting_period"]["end"]} 공시 매출은 {Decimal(segment["segment_revenue"])/1000000:,.2f}백만 USD이며 {trend}입니다. '
        else:lead=operational['statement'].replace('에 대한 회사 공시 설명','의 사업 설명')+'을 확인했습니다. '
        cash_context=f'공시 영업현금흐름 {Decimal(cash["value"])/1000000:,.2f}백만 USD'
        if capex:cash_context+=f'와 현금 설비취득 {Decimal(capex["value"])/1000000:,.2f}백만 USD'
        value['investment_theses']=[{'statement':lead+cash_context+'를 함께 보면 사업 확장이 현금 회수로 이어지는지 점검할 수 있습니다.',
            'kind':'ANALYST_INTERPRETATION','evidence_kind':'DERIVED','supporting_facts':supporting,'financial_context':context,'source_refs':list(dict.fromkeys(sources)),
            'uncertainty':'사업 설명은 미래 성장의 입증이 아니며, 현재 배수의 적정성과 투자 회수 시점은 미확인입니다.'}]
        value['counter_theses']=[{'statement':(risk['statement'] if risk else operational['statement'])+'은 위 논거의 제약입니다. '+cash_context+'의 차이를 확인해야 하며, 재무 숫자만으로 수요 지속이나 투자 회수를 입증할 수 없습니다.',
            'kind':'ANALYST_INTERPRETATION','evidence_kind':'DERIVED','supporting_facts':([risk['id']] if risk else [operational['id']])+context,
            'financial_context':context,'source_refs':list(dict.fromkeys((risk or operational)['source_refs']+cash['source_refs']+(capex['source_refs'] if capex else []))),
            'uncertainty':'미래 수요·가격·투자 회수 규모를 정량 예측하지 않았습니다.'}]
        value['estimates']=[{'id':'scenario:'+name,'name':name,'value':statement,'unit':None,'as_of':at,
            'evidence_kind':'ESTIMATED','kind':'SCENARIO_ASSUMPTION','source_refs':list(dict.fromkeys(sources+(risk['source_refs'] if risk else []))),
            'assumptions':[statement],'limitations':['정성적 조건 분기이며 확률·목표가·수치 전망이 아닙니다.']} for name,statement in [
                ('bull',(segment['segment_name']+' 사업부' if segment else operational['statement'])+'의 수요가 확대되고 '+cash_context+'의 회수 관계가 개선되는 경우'),
                ('base',(segment['segment_name']+' 사업부' if segment else operational['statement'])+'의 수익 구조가 유지되고 공시된 설비투자 부담이 지속되는 경우'),
                ('bear',(risk['statement'] if risk else operational['statement'])+'의 제약이 현실화되어 영업현금흐름과 투자 회수에 부담이 생기는 경우')]]


def validate(value):
    refs=value['source_references'];ids={x['id'] for x in value.get('business_profile',[])+value['facts']}
    ids.update(x['id'] for g in value.get('business_segments',[]) for x in g['segments'])
    for f in value.get('business_profile',[]):
        if f['kind']!='SOURCE_FACT' or not f['source_refs'] or any(r not in refs for r in f['source_refs']):raise ValueError('UNSUPPORTED_BUSINESS_FACT')
    for f in value.get('investment_theses',[])+value.get('counter_theses',[]):
        if f['kind']!='ANALYST_INTERPRETATION' or not f['financial_context'] or any(i not in ids for i in f['supporting_facts']):raise ValueError('UNSUPPORTED_THESIS')
