"""ILLUSTRATIVE fixtures only; no real people, market labels or model calls."""
from copy import deepcopy
from datetime import datetime,timezone,timedelta
import json
import pytest
import shutil
import subprocess
from research.sec_transport import now
from research.validation import stable_hash
from extensions.tests.test_exposure_v3 import packet
from extensions.research_v4 import labels as original,segmentation
from extensions.research_v4.tests.test_v4 import proposal
from extensions.reliability_recovery import audit,labels,seal


def make_source():return original.corpus([packet(facts=[])])
def reseal(value):return seal({k:v for k,v in value.items() if k!='record_sha256'})


def review(task,direction='increase'):
    case=task['cases'][0];text=case['text']
    return {'assignment_sha256':task['record_sha256'],'reviewer_id':task['reviewer_id'],'role':task['role'],
        'reviewer_kind':'human','outcome_and_model_blinded':True,'other_primary_answers_blinded':task['role']=='primary',
        'cases':[{'case_id':case['case_id'],'labels':[{'start':0,'end':len(text),'exact_quote':text,
            'subject_quote':'Interest rates','predicate_quote':'increased','economic_variable':'interest_rate',
            'direction':direction,'assertion':'observation','negation_scope':'none','temporal_reference':'past',
            'condition_quotes':[],'time_quotes':[]}],'no_proposition':False,'insufficient_context':False,
            'reason':'ILLUSTRATIVE declared-human fixture only','reviewed_at':now()}]}


def imports(tmp_path,conflict=True):
    source=make_source();a=labels.assignment(source,'ILLUSTRATIVE_A');b=labels.assignment(source,'ILLUSTRATIVE_B')
    ra=labels.import_review(a,review(a),tmp_path/'a.json')
    rb=labels.import_review(b,review(b,'decrease' if conflict else 'increase'),tmp_path/'b.json')
    return source,ra,rb


def test_denominators_separate_missing_wrong_approval_and_actual_schema():
    u=segmentation.source_units([packet(facts=[])])[0][0];p=proposal(u);extra={**p,'economic_variable':'production_volume','event_type':'energy_supply'}
    props=[{'raw':r,'decision':'SUPPORTED','binding':{},'reasons':[],
            'assessment':{'status':'COMPLETED','raw':{'judgment':'consistent'}}} for r in [p,extra]]
    gold=[{'unit_id':u['unit_id'],'propositions':[p,{**p,'economic_variable':'energy_price','event_type':'commodity_price'}]}]
    saved=deepcopy(gold)
    value={'rows':[{'unit':u,'row':{'status':'COMPLETED','extract':{'status':'COMPLETED','raw':{'propositions':[p,extra],'omission_reason':'none'}},'propositions':props}}]}
    result=audit.authored(value,gold)
    assert result['metrics']['exact_reference_raw_recall']['value']==.5
    assert result['metrics']['actual_schema_acceptance']['value']==1
    assert result['counts']['supported_reference_disagreements']==1
    assert result['counts']['answerable_missing_references']==1
    assert result['metrics']['exact_correct_proposal_rejection']['denominator']==1
    assert result['metrics']['adjudicated_semantic_precision']=='NOT_MEASURED'
    assert gold==saved and not result['frozen_scores_changed']
    with pytest.raises(ValueError):audit.authored({'rows':[]},gold)


def test_literal_source_subquote_is_not_automatically_semantically_correct():
    u=segmentation.source_units([packet(facts=[])])[0][0];p=proposal(u,exact_quote='rates increased')
    obs=audit.structural_observations(u,p)
    assert obs['source_subquote_not_offered'] and obs['semantic_equivalence']=='NOT_ADJUDICATED'


def test_normalized_subject_code_in_quote_slot_is_diagnosed_not_repaired():
    u=segmentation.source_units([packet(facts=[])])[0][0];p=proposal(u,subject_quote='interest_rates');before=deepcopy(p)
    obs=audit.structural_observations(u,p)
    assert not obs['subject_verbatim_in_source'] and obs['underscore_to_space_casefold_subject_occurs']
    assert p==before and obs['semantic_equivalence']=='NOT_ADJUDICATED'


def test_always_reject_does_not_pass_critic_gate_with_imbalanced_labels():
    cases=[];gold=[];rows=[]
    for i in range(6):
        ctx={'ISSUER':{'cik':'1'},'FACTS':[],'PROPOSITION':{'proposition_id':'claim'},
            'EVIDENCE':[{'paragraph_id':str(i),'proposition_id':'source'}]}
        for arm in ['reference','corrupted']:
            cid=str(i)+arm;cases.append({'id':cid,'arm':arm,'messages':[{}, {'content':json.dumps(ctx)}]})
            gold.append({'id':cid,'allowed_verdicts':['SUPPORTED_CONDITIONAL'] if arm=='reference' and i<3 else ['INSUFFICIENT_EVIDENCE'] if arm=='reference' else ['UNSUPPORTED','CONTRADICTED']})
            rows.append({'id':cid,'raw':{'verdict':'UNSUPPORTED'}})
    r=audit.critic(cases,gold,{'rows':rows,'metrics':{}},{})
    assert r['nominal_binary_label_counts']=={'positive':3,'negative':9}
    assert r['nominal_balanced_accuracy']==.5 and not r['all_reject_gate_pass']
    assert r['causal_over_rejection_rate']=='NOT_IDENTIFIED'


@pytest.mark.parametrize('key',['model_output','other_reviewer_answers','gold'])
def test_primary_assignment_rejects_contaminated_corpus_fields(key):
    source=make_source();source['cases'][0][key]='ILLUSTRATIVE_HIDDEN_ANSWER';source=reseal(source)
    with pytest.raises(ValueError,match='EXTRA_FIELDS'):labels.assignment(source,'ILLUSTRATIVE_A')


def test_corpus_source_hash_and_case_identity_are_checked():
    source=make_source();source['cases'][0]['text']+=' changed';source=reseal(source)
    with pytest.raises(ValueError,match='TEXT_CHANGED'):labels.validate_corpus(source)


def test_two_complete_primaries_third_adjudication_and_immutable_gold(tmp_path):
    source,a,b=imports(tmp_path);diff=labels.compare(a,b);assert diff['disagreements']
    with pytest.raises(ValueError,match='ADJUDICATION_REQUIRED'):labels.finalize_gold(source,a,b,None,tmp_path/'bad.json')
    task=labels.assignment(source,'ILLUSTRATIVE_C',role='adjudicator',reviews=[a,b])
    third=labels.import_review(task,review(task),tmp_path/'third.json')
    gold=labels.finalize_gold(source,a,b,third,tmp_path/'gold.json')
    assert not gold['is_unseen_holdout'] and not gold['independently_validated']
    with pytest.raises(FileExistsError):labels.finalize_gold(source,a,b,third,tmp_path/'gold.json')
    with pytest.raises(ValueError,match='THIRD'):labels.assignment(source,'ILLUSTRATIVE_A',role='adjudicator',reviews=[a,b])


def test_adjudication_binds_the_exact_primary_pair(tmp_path):
    source,a,b=imports(tmp_path);task=labels.assignment(source,'ILLUSTRATIVE_C',role='adjudicator',reviews=[a,b])
    third=labels.import_review(task,review(task),tmp_path/'c.json')
    alternate=deepcopy(a);alternate['review']['cases'][0]['reason']='ILLUSTRATIVE corrected rationale';alternate=reseal(alternate)
    with pytest.raises(ValueError,match='PAIR_CHANGED'):labels.finalize_gold(source,alternate,b,third,tmp_path/'gold.json')


def test_incomplete_primary_cannot_be_sealed(tmp_path):
    source,a,b=imports(tmp_path,conflict=False);partial=deepcopy(a);partial['review']['cases']=[];partial['complete']=False;partial=reseal(partial)
    with pytest.raises(ValueError,match='NOT_READY'):labels.finalize_gold(source,partial,b,None,tmp_path/'gold.json')


@pytest.mark.parametrize('fault',['duplicate','extra_field','backdate','absence_uncertain','bad_quote'])
def test_invalid_human_reviews_fail_closed(fault):
    task=labels.assignment(make_source(),'ILLUSTRATIVE_A');r=review(task)
    if fault=='duplicate':r['cases'][0]['labels']*=2
    if fault=='extra_field':r['cases'][0]['labels'][0]['model_verdict']='SUPPORTED'
    if fault=='backdate':r['cases'][0]['reviewed_at']='2000-01-01T00:00:00Z'
    if fault=='absence_uncertain':r['cases'][0].update(labels=[],no_proposition=True,insufficient_context=True)
    if fault=='bad_quote':r['cases'][0]['labels'][0]['end']-=1
    with pytest.raises(ValueError):labels.validate_review(task,r)


def test_label_presentation_order_is_not_a_semantic_disagreement(tmp_path):
    source=make_source();ta=labels.assignment(source,'ILLUSTRATIVE_A');tb=labels.assignment(source,'ILLUSTRATIVE_B')
    a=review(ta);b=review(tb)
    second={**a['cases'][0]['labels'][0],'direction':'unknown'}
    a['cases'][0]['labels'].append(second);b['cases'][0]['labels']=[second,b['cases'][0]['labels'][0]]
    ra=labels.import_review(ta,a,tmp_path/'a.json');rb=labels.import_review(tb,b,tmp_path/'b.json')
    assert not labels.compare(ra,rb)['disagreements']


def test_primary_html_has_restore_edit_controls_but_no_other_answers():
    task=labels.assignment(make_source(),'ILLUSTRATIVE_A');page=labels.render(task)
    assert 'id="remove"' in page and 'id="reset"' in page and 'id="restore"' in page
    assert 'v.reviewer_id!==TASK.reviewer_id' in page
    assert task['alternatives'] is None and not task['model_outputs_included']


def test_no_model_needed_for_resource_audit():
    assert audit.runtime_stats([])['additional_recovery_inference_calls']==0


def test_label_ui_save_edit_restore_and_cross_reviewer_guard_in_node(tmp_path):
    if not shutil.which('node'):pytest.skip('Node unavailable: DOM simulation not run')
    task=labels.assignment(make_source(),'ILLUSTRATIVE_A');page=tmp_path/'review.html';page.write_text(labels.render(task),encoding='utf-8')
    script=r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8'),code=html.split('<script>')[1].split('</script>')[0];
const nodes={};let downloaded;
class Element{constructor(){this.value='';this.checked=false;this.textContent='';this.children=[]}
set id(v){this._id=v;nodes[v]=this}get id(){return this._id}appendChild(x){this.children.push(x)}
add(x){if(!this.children.length)this.value=x.value;this.children.push(x)}contains(x){return x===this}click(){}}
for(const m of html.matchAll(/id="([^"]+)"/g)){let x=new Element();x.id=m[1]}
const document={getElementById:id=>{assert(nodes[id],id);return nodes[id]},createElement:()=>new Element()};
let selectedText='';const window={getSelection:()=>({rangeCount:1,getRangeAt:()=>({startContainer:nodes.source,endContainer:nodes.source,startOffset:0,
toString:()=>selectedText,cloneRange:()=>({selectNodeContents(){},setEnd(){},toString:()=>''})})})};
const context={document,window,Option:function(t,v){this.text=t;this.value=v},Blob:function(parts){this.parts=parts},
URL:{createObjectURL:b=>{downloaded=JSON.parse(b.parts[0]);return 'blob:fixture'},revokeObjectURL(){}},console};
vm.runInNewContext(code,context,{timeout:1000});
const set=(k,v)=>nodes[k].value=v;
function add(){selectedText=nodes.source.textContent;nodes.select.onclick();set('subject_quote','Interest rates');set('predicate_quote','increased');
for(const [k,v]of Object.entries({economic_variable:'interest_rate',direction:'increase',assertion:'observation',negation_scope:'none',temporal_reference:'past'}))set(k,v);
nodes.add.onclick()}
(async()=>{set('reason','ILLUSTRATIVE local UI test');nodes.blind.checked=true;add();nodes.save.onclick();nodes.download.onclick();
assert.equal(downloaded.cases[0].labels.length,1);const saved=JSON.parse(JSON.stringify(downloaded));
add();nodes.download.onclick();assert.equal(downloaded.cases[0].labels.length,1,'unsaved draft changed confirmed answer');
nodes.remove.onclick();assert.equal(JSON.parse(nodes.drafts.textContent).length,1);
nodes.reset.onclick();nodes.download.onclick();assert.equal(downloaded.cases.length,0);
await nodes.restore.onchange({target:{files:[{text:async()=>JSON.stringify(saved)}]}});nodes.download.onclick();assert.equal(downloaded.cases.length,1);
const foreign={...saved,reviewer_id:'ILLUSTRATIVE_B'};await nodes.restore.onchange({target:{files:[{text:async()=>JSON.stringify(foreign)}]}});
assert(nodes.status.textContent.includes('복원 거절'));nodes.download.onclick();assert.equal(downloaded.reviewer_id,'ILLUSTRATIVE_A');
console.log(JSON.stringify({save_edit_restore:'PASS',cross_reviewer_restore:'REJECTED'}))})().catch(e=>{console.error(e);process.exitCode=1});
'''
    harness=tmp_path/'ui.cjs';harness.write_text(script,encoding='utf-8')
    result=subprocess.run(['node',str(harness),str(page)],capture_output=True,text=True,encoding='utf-8',timeout=15)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['cross_reviewer_restore']=='REJECTED'
