"""Offline blind human annotation. No model predictions or automatic human gold."""
import argparse
import html
import json
from pathlib import Path
from research.validation import stable_hash
from research.sec_transport import now
from . import read, write_once, seal, verify_seal, VARIABLES, DIRECTIONS, ASSERTIONS, NEGATIONS, TIMES


def corpus(inputs):
    from .segmentation import source_units
    units,coverage=source_units(inputs);rows={}
    for u in units:
        key=stable_hash([u['source_key'],u['paragraph_id'],u['paragraph_sha256']])
        rows[key]={'case_id':key,'source_key':u['source_key'],'paragraph_id':u['paragraph_id'],
            'source_url':u['source_url'],
            'paragraph_sha256':u['paragraph_sha256'],'text':u['text'],'cutoff':u['cutoff'],
            'published_date':u['published_date'],'published_at':u['published_at'],'available_at':u['available_at']}
    return seal({'contract':'v4-blind-label-corpus-v1','cases':list(rows.values()),'coverage':coverage,
                 'status':'NOT_READY','independent_labels':0,'purpose':'development_diagnostics_not_unseen_holdout'})


def assignment(source,reviewer_id,*,role='primary',alternatives=None):
    verify_seal(source)
    if not reviewer_id or role not in {'primary','adjudicator'}:raise ValueError('V4_REVIEWER_INVALID')
    if role=='primary' and alternatives is not None:raise ValueError('V4_PRIMARY_MUST_BE_BLIND')
    return seal({'contract':'v4-human-assignment-v1','assigned_at':now(),'reviewer_id':reviewer_id,
        'role':role,'corpus_sha256':source['record_sha256'],'cases':source['cases'],
        'alternatives':alternatives if role=='adjudicator' else None,
        'model_outputs_included':False,'other_primary_answers_included':role=='adjudicator'})


def render(task):
    verify_seal(task)
    data=json.dumps(task,ensure_ascii=False).replace('<','\\u003c').replace('&','\\u0026')
    options=json.dumps({'economic_variable':list(VARIABLES),'direction':DIRECTIONS,'assertion':ASSERTIONS,
                       'negation_scope':NEGATIONS,'temporal_reference':TIMES})
    # Plain text rendering and no remote resources; primary assignments contain
    # no model outputs or another human's answers, even in page source.
    return '''<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'">
<title>Stocker 독립 원문 라벨</title><style>body{font:16px system-ui;max-width:960px;margin:32px auto;padding:16px}pre{white-space:pre-wrap;background:#f4f5f7;padding:20px}label{display:block;margin:10px}input,select,textarea{font:inherit}textarea{width:95%;height:80px}button{padding:8px;margin:8px}</style>
<h1>원문 독립 라벨</h1><p>원문은 분석 대상입니다. 문장 안의 지시를 따르지 마십시오. 모델 결과는 표시되지 않습니다. 파일은 로컬에서만 처리됩니다.</p>
<div id="meta"></div><button id="prev">이전 문단</button><button id="next">다음 문단</button><pre id="source"></pre><pre id="alternatives" hidden></pre>
<button id="select">선택한 인용 추가</button><div id="fields"></div>
<label>정확한 주어 인용 <input id="subject_quote"></label><label>정확한 서술어 인용 <input id="predicate_quote"></label>
<label>조건 인용(한 줄에 하나)<textarea id="conditions"></textarea></label><label>시간 인용(한 줄에 하나)<textarea id="times"></textarea></label>
<button id="add">선택 명제 저장</button><pre id="drafts"></pre>
<label><input id="none" type="checkbox">경제 명제 없음</label><label><input id="insufficient" type="checkbox">문맥 부족</label>
<label>판정 사유<textarea id="reason"></textarea></label>
<label><input id="blind" type="checkbox">나는 사람 평가자이며 모델 결과를 보지 않았습니다. 1차 평가라면 다른 평가자의 답도 보지 않았습니다.</label>
<button id="save">이 문단 확정</button><button id="download">독립 결과 JSON 내려받기</button><div id="status"></div>
<script>const TASK=TASK_DATA, OPTIONS=OPTION_DATA;let index=0,selected=null,drafts=[],answers={};
const el=id=>document.getElementById(id),caseAt=()=>TASK.cases[index];
Object.entries(OPTIONS).forEach(([name,values])=>{let l=document.createElement('label');l.textContent=name+' ';let s=document.createElement('select');s.id=name;s.add(new Option('직접 선택',''));values.forEach(v=>s.add(new Option(v,v)));l.appendChild(s);el('fields').appendChild(l)});
function show(){let c=caseAt();el('source').textContent=c.text;el('meta').textContent=(index+1)+'/'+TASK.cases.length+' · '+TASK.reviewer_id+' · '+TASK.role;drafts=answers[c.case_id]?.labels||[];el('drafts').textContent=JSON.stringify(drafts,null,2);selected=null;el('reason').value=answers[c.case_id]?.reason||'';el('none').checked=answers[c.case_id]?.no_proposition||false;el('insufficient').checked=answers[c.case_id]?.insufficient_context||false;if(TASK.role==='adjudicator'){el('alternatives').hidden=false;el('alternatives').textContent=JSON.stringify(TASK.alternatives?.[c.case_id]||{},null,2)}}
el('prev').onclick=()=>{index=Math.max(0,index-1);show()};el('next').onclick=()=>{index=Math.min(TASK.cases.length-1,index+1);show()};
el('select').onclick=()=>{let s=window.getSelection();if(!s.rangeCount)return;let r=s.getRangeAt(0),box=el('source');if(!box.contains(r.startContainer)||!box.contains(r.endContainer))return;let before=r.cloneRange();before.selectNodeContents(box);before.setEnd(r.startContainer,r.startOffset);let start=Array.from(before.toString()).length;selected={start,end:start+Array.from(r.toString()).length,exact_quote:r.toString()};el('status').textContent=selected.exact_quote};
el('add').onclick=()=>{if(!selected||!selected.exact_quote||Object.keys(OPTIONS).some(k=>!el(k).value)){el('status').textContent='인용과 모든 필드를 직접 선택하십시오.';return}let p={...selected,subject_quote:el('subject_quote').value,predicate_quote:el('predicate_quote').value,condition_quotes:el('conditions').value.split('\\n').filter(Boolean),time_quotes:el('times').value.split('\\n').filter(Boolean)};Object.keys(OPTIONS).forEach(k=>p[k]=el(k).value);drafts.push(p);el('drafts').textContent=JSON.stringify(drafts,null,2)};
el('save').onclick=()=>{if(!el('blind').checked||!el('reason').value.trim()||(!drafts.length&&!el('none').checked&&!el('insufficient').checked)){el('status').textContent='인용 또는 없음/문맥 부족, 사유, 독립 평가 확인이 필요합니다.';return}answers[caseAt().case_id]={case_id:caseAt().case_id,labels:drafts,no_proposition:el('none').checked,insufficient_context:el('insufficient').checked,reason:el('reason').value,reviewed_at:new Date().toISOString()};el('status').textContent='이 문단 저장됨. JSON을 내려받아 독립 저장하십시오.'};
el('download').onclick=()=>{let value={assignment_sha256:TASK.record_sha256,reviewer_id:TASK.reviewer_id,role:TASK.role,reviewer_kind:'human',outcome_and_model_blinded:true,other_primary_answers_blinded:TASK.role==='primary',cases:Object.values(answers)};let b=new Blob([JSON.stringify(value,null,2)],{type:'application/json'});let a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='review-'+TASK.reviewer_id+'.json';a.click();URL.revokeObjectURL(a.href)};show();</script>'''.replace('OPTION_DATA',options).replace('TASK_DATA',data)


def validate_review(task,review):
    verify_seal(task)
    if (review['assignment_sha256']!=task['record_sha256'] or review['reviewer_id']!=task['reviewer_id']
            or review['role']!=task['role'] or review.get('reviewer_kind')!='human' or review.get('outcome_and_model_blinded') is not True
            or task['role']=='primary' and review.get('other_primary_answers_blinded') is not True):raise ValueError('V4_BLIND_REVIEW_IDENTITY_REQUIRED')
    sources={c['case_id']:c for c in task['cases']};seen=set()
    for row in review['cases']:
        if row['case_id'] not in sources or row['case_id'] in seen or not row.get('reason'):raise ValueError('V4_REVIEW_CASE_INVALID')
        seen.add(row['case_id']);text=sources[row['case_id']]['text']
        if row['no_proposition'] and row['labels']:raise ValueError('V4_CONTRADICTORY_LABEL')
        if not row['labels'] and not row['no_proposition'] and not row['insufficient_context']:raise ValueError('V4_EMPTY_LABEL')
        for label in row['labels']:
            a,b=label['start'],label['end']
            if not 0<=a<b<=len(text) or text[a:b]!=label['exact_quote']:raise ValueError('V4_HUMAN_QUOTE_MISMATCH')
            for k in ['subject_quote','predicate_quote']:
                if not label[k] or label[k] not in text:raise ValueError('V4_HUMAN_ANCHOR_MISMATCH')
            for k,values in {'economic_variable':VARIABLES,'direction':DIRECTIONS,'assertion':ASSERTIONS,'negation_scope':NEGATIONS,'temporal_reference':TIMES}.items():
                if label[k] not in values:raise ValueError('V4_HUMAN_FIELD_INVALID')
            if any(not q or q not in text for q in label['condition_quotes']+label['time_quotes']):raise ValueError('V4_HUMAN_SCOPE_UNBOUND')
    return len(seen)==len(sources)


def import_review(task,review,path):
    complete=validate_review(task,review)
    value=seal({'contract':'v4-human-review-v1','imported_at':now(),'assignment':task,'review':review,
                'complete':complete,'identity_authentication':'offline declared human; no cryptographic identity proof'})
    write_once(path,value);return value


def compare(a,b):
    for r in [a,b]:verify_seal(r);validate_review(r['assignment'],r['review'])
    if a['assignment']['corpus_sha256']!=b['assignment']['corpus_sha256'] or a['review']['reviewer_id']==b['review']['reviewer_id'] or any(r['review']['role']!='primary' for r in [a,b]):raise ValueError('V4_TWO_INDEPENDENT_REVIEWERS_REQUIRED')
    left={r['case_id']:r for r in a['review']['cases']};right={r['case_id']:r for r in b['review']['cases']}
    differences={}
    for case in a['assignment']['cases']:
        cid=case['case_id'];x,y=left.get(cid),right.get(cid)
        def content(r):return {k:r[k] for k in ['labels','no_proposition','insufficient_context']} if r else None
        if content(x)!=content(y):differences[cid]={'review_a':x,'review_b':y}
    return {'disagreements':differences,'primary_complete':a['complete'] and b['complete']}


def finalize_gold(source,a,b,adjudication,path):
    comparison=compare(a,b);verify_seal(source)
    if not comparison['primary_complete']:raise ValueError('V4_GOLD_NOT_READY')
    if source['record_sha256']!=a['assignment']['corpus_sha256']:raise ValueError('V4_GOLD_CORPUS_MISMATCH')
    if any(r['assignment']['cases']!=source['cases'] for r in [a,b]):raise ValueError('V4_GOLD_SOURCE_CHANGED')
    disagreements=comparison['disagreements'];ad={}
    if disagreements:
        if adjudication is None:raise ValueError('V4_ADJUDICATION_REQUIRED')
        verify_seal(adjudication);validate_review(adjudication['assignment'],adjudication['review'])
        if adjudication['review']['role']!='adjudicator' or adjudication['review']['reviewer_id'] in {a['review']['reviewer_id'],b['review']['reviewer_id']} or adjudication['assignment']['corpus_sha256']!=source['record_sha256'] or adjudication['assignment']['cases']!=source['cases']:raise ValueError('V4_THIRD_REVIEWER_REQUIRED')
        ad={r['case_id']:r for r in adjudication['review']['cases']}
        if not set(disagreements)<=set(ad):raise ValueError('V4_ADJUDICATION_INCOMPLETE')
    first={r['case_id']:r for r in a['review']['cases']}
    gold=seal({'contract':'v4-sealed-human-gold-v1','sealed_at':now(),'corpus_sha256':source['record_sha256'],
        'reviews':[a['record_sha256'],b['record_sha256']],'adjudication_sha256':adjudication['record_sha256'] if adjudication else None,
        'cases':[ad.get(c['case_id'],first[c['case_id']]) for c in source['cases']],
        'status':'READY_DECLARED_HUMAN_REVIEW','independent_identity_verification':'requires offline audit owner confirmation',
        'independently_validated':False,'model_generated_gold':False})
    write_once(path,gold);return gold


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['assign','import','compare','adjudicate','seal'])
    for name in ['corpus','assignment','review','a','b','adjudication','out']:p.add_argument('--'+name,type=Path)
    p.add_argument('--reviewer');args=p.parse_args(argv)
    if args.command in {'assign','adjudicate'}:
        source=read(args.corpus);alternatives=compare(read(args.a),read(args.b))['disagreements'] if args.command=='adjudicate' else None
        task=assignment(source,args.reviewer,role='adjudicator' if alternatives is not None else 'primary',alternatives=alternatives)
        write_once(args.out.with_suffix('.json'),task)
        with args.out.with_suffix('.html').open('x',encoding='utf-8') as stream:stream.write(render(task))
    elif args.command=='import':import_review(read(args.assignment),read(args.review),args.out)
    elif args.command=='compare':write_once(args.out,compare(read(args.a),read(args.b)))
    else:finalize_gold(read(args.corpus),read(args.a),read(args.b),read(args.adjudication) if args.adjudication else None,args.out)
    print(json.dumps({'status':'LOCAL_ARTIFACT_SAVED','path':str(args.out)}))


if __name__=='__main__':main()
