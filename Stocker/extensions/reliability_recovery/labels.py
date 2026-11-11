"""Versioned offline labeling safeguards; never modifies frozen v4 tools or gold."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from research.rights import as_utc
from research.validation import stable_hash
from extensions.research_v4 import labels as old
from . import read,write_once,seal,verify_seal

CASE_KEYS={'case_id','source_key','paragraph_id','source_url','paragraph_sha256','text','cutoff','published_date','published_at','available_at'}
LABEL_KEYS={'start','end','exact_quote','subject_quote','predicate_quote','economic_variable','direction','assertion',
            'negation_scope','condition_quotes','time_quotes','temporal_reference'}


def validate_corpus(source):
    verify_seal(source)
    if source.get('contract')!='v4-blind-label-corpus-v1' or not source.get('cases'):raise ValueError('RECOVERY_CORPUS_INVALID')
    seen=set()
    for case in source['cases']:
        if set(case)!=CASE_KEYS:raise ValueError('RECOVERY_PRIMARY_CASE_EXTRA_FIELDS')
        if case['case_id'] in seen:raise ValueError('RECOVERY_DUPLICATE_CASE')
        seen.add(case['case_id'])
        if hashlib.sha256(case['text'].encode()).hexdigest()!=case['paragraph_sha256']:raise ValueError('RECOVERY_SOURCE_TEXT_CHANGED')
        if case['case_id']!=stable_hash([case['source_key'],case['paragraph_id'],case['paragraph_sha256']]):raise ValueError('RECOVERY_CASE_ID_CHANGED')
        url=urlsplit(case['source_url'])
        if url.scheme!='https' or url.netloc not in {'www.eia.gov','www.federalreserve.gov','eia.gov','federalreserve.gov'}:raise ValueError('RECOVERY_SOURCE_NOT_APPROVED_FAMILY')
        if as_utc(case['available_at'])>=as_utc(case['cutoff']) or case['published_date']>as_utc(case['cutoff']).date().isoformat():raise ValueError('RECOVERY_FUTURE_SOURCE')
    return source


def assignment(source,reviewer_id,*,role='primary',reviews=None):
    validate_corpus(source)
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',reviewer_id):raise ValueError('RECOVERY_USE_PSEUDONYMOUS_REVIEWER_ID')
    alternatives=None;hashes=None
    if role=='adjudicator':
        if not reviews or len(reviews)!=2:raise ValueError('RECOVERY_TWO_REVIEW_FILES_REQUIRED')
        result=compare(*reviews)
        if reviewer_id in {r['review']['reviewer_id'] for r in reviews}:raise ValueError('RECOVERY_THIRD_REVIEWER_DISTINCT')
        if reviews[0]['assignment']['corpus_sha256']!=source['record_sha256']:raise ValueError('RECOVERY_REVIEW_CORPUS_CHANGED')
        alternatives=result['disagreements'];hashes=sorted(r['record_sha256'] for r in reviews)
    elif role!='primary' or reviews is not None:raise ValueError('RECOVERY_PRIMARY_MUST_BE_BLIND')
    value=old.assignment(source,reviewer_id,role=role,alternatives=alternatives)
    value.pop('record_sha256')
    value.update(contract='recovery-human-assignment-v2',source_purpose=source['purpose'],
                 comparison_review_hashes=hashes,reviewer_identity_verified=False)
    return seal(value)


def validate_review(task,review):
    verify_seal(task)
    if task['contract']!='recovery-human-assignment-v2':raise ValueError('RECOVERY_ASSIGNMENT_VERSION')
    expected={'assignment_sha256','reviewer_id','role','reviewer_kind','outcome_and_model_blinded','other_primary_answers_blinded','cases'}
    if set(review)!=expected:raise ValueError('RECOVERY_REVIEW_EXTRA_FIELDS')
    try:complete=old.validate_review(task,review)
    except (KeyError,TypeError) as error:raise ValueError('RECOVERY_REVIEW_STRUCTURE_INVALID') from error
    for row in review['cases']:
        if set(row)!={'case_id','labels','no_proposition','insufficient_context','reason','reviewed_at'}:raise ValueError('RECOVERY_CASE_REVIEW_EXTRA_FIELDS')
        if type(row['no_proposition']) is not bool or type(row['insufficient_context']) is not bool:raise ValueError('RECOVERY_REVIEW_FLAGS_INVALID')
        if row['no_proposition'] and row['insufficient_context']:raise ValueError('RECOVERY_ABSENCE_VS_UNCERTAINTY')
        if not isinstance(row['reason'],str) or not row['reason'].strip():raise ValueError('RECOVERY_REASON_REQUIRED')
        if as_utc(row['reviewed_at'])<as_utc(task['assigned_at']):raise ValueError('RECOVERY_REVIEW_PREDATES_ASSIGNMENT')
        duplicates=set()
        for label in row['labels']:
            if set(label)!=LABEL_KEYS or type(label['start']) is not int or type(label['end']) is not int:raise ValueError('RECOVERY_LABEL_FIELDS_INVALID')
            h=stable_hash(label)
            if h in duplicates:raise ValueError('RECOVERY_DUPLICATE_LABEL')
            duplicates.add(h)
    return complete


def import_review(task,review,path):
    complete=validate_review(task,review)
    from research.sec_transport import now
    result=seal({'contract':'recovery-human-review-v2','imported_at':now(),'assignment':task,'review':review,
                 'complete':complete,'identity_authentication':'declared human; offline independence verification required'})
    write_once(path,result);return result


def compare(a,b):
    for r in [a,b]:
        verify_seal(r)
        if validate_review(r['assignment'],r['review'])!=r['complete']:raise ValueError('RECOVERY_COMPLETENESS_CHANGED')
    if a['review']['role']!='primary' or b['review']['role']!='primary' or a['review']['reviewer_id']==b['review']['reviewer_id']:
        raise ValueError('RECOVERY_TWO_DISTINCT_PRIMARIES')
    if a['assignment']['corpus_sha256']!=b['assignment']['corpus_sha256'] or a['assignment']['cases']!=b['assignment']['cases']:
        raise ValueError('RECOVERY_REVIEW_CORPUS_CHANGED')
    first={r['case_id']:r for r in a['review']['cases']};second={r['case_id']:r for r in b['review']['cases']};differences={}
    def normalized(row):
        if row is None:return None
        return {'labels':sorted(row['labels'],key=lambda x:json.dumps(x,sort_keys=True)),
                'no_proposition':row['no_proposition'],'insufficient_context':row['insufficient_context']}
    for case in a['assignment']['cases']:
        cid=case['case_id'];x,y=first.get(cid),second.get(cid)
        if normalized(x)!=normalized(y):differences[cid]={'review_a':x,'review_b':y}
    return {'disagreements':differences,'primary_complete':a['complete'] and b['complete'],
            'semantic_equivalence_automatically_adjudicated':False}


def finalize_gold(source,a,b,third,path):
    validate_corpus(source);result=compare(a,b)
    if not result['primary_complete']:raise ValueError('RECOVERY_GOLD_NOT_READY')
    for r in [a,b]:
        if r['assignment']['corpus_sha256']!=source['record_sha256'] or r['assignment']['cases']!=source['cases']:raise ValueError('RECOVERY_GOLD_SOURCE_CHANGED')
    resolved={}
    if third is not None:
        verify_seal(third);validate_review(third['assignment'],third['review'])
        if third['review']['role']!='adjudicator' or third['review']['reviewer_id'] in {a['review']['reviewer_id'],b['review']['reviewer_id']}:raise ValueError('RECOVERY_THIRD_REVIEWER_DISTINCT')
        task=third['assignment']
        if task['corpus_sha256']!=source['record_sha256'] or task['cases']!=source['cases'] or task['comparison_review_hashes']!=sorted([a['record_sha256'],b['record_sha256']]):raise ValueError('RECOVERY_ADJUDICATION_PAIR_CHANGED')
    if result['disagreements']:
        if third is None:raise ValueError('RECOVERY_ADJUDICATION_REQUIRED')
        resolved={r['case_id']:r for r in third['review']['cases']}
        if not set(result['disagreements'])<=set(resolved):raise ValueError('RECOVERY_ADJUDICATION_INCOMPLETE')
    first={r['case_id']:r for r in a['review']['cases']}
    from research.sec_transport import now
    value=seal({'contract':'recovery-sealed-human-gold-v2','sealed_at':now(),'corpus_sha256':source['record_sha256'],
        'purpose':source['purpose'],'reviews':[a['record_sha256'],b['record_sha256']],
        'adjudication_sha256':third['record_sha256'] if third else None,
        'cases':[resolved.get(c['case_id'],first[c['case_id']]) for c in source['cases']],
        'status':'READY_DECLARED_HUMAN_REVIEW','independent_identity_verified':False,
        'is_unseen_holdout':False,'independently_validated':False,'model_generated_gold':False})
    write_once(path,value);return value


def render(task):
    verify_seal(task)
    page=old.render(task)
    # Draft editing must not mutate an already confirmed annotation through a
    # shared list reference; only the explicit Save action commits a snapshot.
    page=page.replace('drafts=answers[c.case_id]?.labels||[];',
        'drafts=JSON.parse(JSON.stringify(answers[c.case_id]?.labels||[]));',1)
    page=page.replace('labels:drafts,','labels:JSON.parse(JSON.stringify(drafts)),',1)
    marker='<button id="save">'
    if marker not in page:raise ValueError('RECOVERY_LEGACY_UI_TEMPLATE_CHANGED')
    page=page.replace(marker,'<button id="remove">마지막 명제 취소</button><button id="reset">이 문단 초기화</button><label>내 저장 결과 불러오기<input id="restore" type="file" accept="application/json"></label>'+marker,1)
    # Inject before first show(), when all controls and state already exist.
    marker=';show();</script>'
    if marker not in page:raise ValueError('RECOVERY_LEGACY_UI_SCRIPT_CHANGED')
    controls=''';
el('remove').onclick=()=>{drafts=drafts.slice(0,-1);el('drafts').textContent=JSON.stringify(drafts,null,2)};
el('reset').onclick=()=>{delete answers[caseAt().case_id];drafts=[];show();el('status').textContent='이 문단만 초기화했습니다.'};
el('restore').onchange=async ev=>{try{let v=JSON.parse(await ev.target.files[0].text());if(v.assignment_sha256!==TASK.record_sha256||v.reviewer_id!==TASK.reviewer_id||v.role!==TASK.role)throw Error('본인 배정 파일이 아닙니다.');let allowed=new Set(TASK.cases.map(c=>c.case_id)), restored={};for(let r of v.cases){if(!allowed.has(r.case_id)||restored[r.case_id])throw Error('문단 ID 오류');restored[r.case_id]=r}answers=restored;show();el('status').textContent='본인의 저장 결과를 복원했습니다.'}catch(e){el('status').textContent='복원 거절: '+e.message}};
const originalSave=el('save').onclick;
el('save').onclick=()=>{if((el('none').checked&&el('insufficient').checked)||(el('none').checked&&drafts.length)){el('status').textContent='명제 있음·없음·문맥 부족을 구분하십시오.';return}originalSave()};
show();</script>'''
    return page.replace(marker,controls,1)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['assign','import','compare','adjudicate','seal'])
    for n in ['corpus','assignment','review','a','b','adjudication','out']:p.add_argument('--'+n,type=Path)
    p.add_argument('--reviewer');args=p.parse_args(argv)
    if args.command in {'assign','adjudicate'}:
        source=read(args.corpus);reviews=[read(args.a),read(args.b)] if args.command=='adjudicate' else None
        task=assignment(source,args.reviewer,role='adjudicator' if reviews else 'primary',reviews=reviews)
        write_once(args.out.with_suffix('.json'),task)
        with args.out.with_suffix('.html').open('x',encoding='utf-8') as stream:stream.write(render(task))
    elif args.command=='import':import_review(read(args.assignment),read(args.review),args.out)
    elif args.command=='compare':write_once(args.out,compare(read(args.a),read(args.b)))
    else:finalize_gold(read(args.corpus),read(args.a),read(args.b),read(args.adjudication) if args.adjudication else None,args.out)
    print(json.dumps({'status':'LOCAL_ARTIFACT_SAVED','path':str(args.out)}))


if __name__=='__main__':main()
