"""Offline diagnostics for the available manuals. Run from the project root."""
from __future__ import annotations

import argparse
import collections
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app import core, semantic
from app.testset_worker import contains

SNAPSHOT = ROOT / 'data/sms.sqlite3'
AUTO = ROOT / 'data/eval_auto.json'
PARAPHRASES = ROOT / '评估报告/同义改写题_10条.json'
ARCHIVED_LLM = ROOT / '评估结果/sms_eval_online.json'
FIVE = {'washer_wm090mja08hk': '01', 'vacuum_g10_plus': '02', 'robotvac_s20': '03',
        'fridge_bcd600': '04', 'ac_kfr51lw': '05'}
SOURCES = {key: next((ROOT / '说明书').glob(prefix + '_*.txt')) for key, prefix in FIVE.items()}
WASH, VAC, ROBOT, FRIDGE, AC = FIVE
ORIG_RETRIEVE = core.retrieve
MAPPINGS = (
    ('有多重', '产品净重'), ('多重', '产品净重'), ('除霜', '化霜'),
    ('能装多少', '总容积'), ('能放多少', '总容积'), ('费电', '功率 耗电量'),
    ('多少瓦', '功率'), ('电流多大', '额定电流'),
)
NARROW = ('它', '这个', '那个', '那', '呢', '还有', '另外')
HANDOFF_EXTRA = ('申请上门维修', '申请维修', '安排维修', '换货', '开票', '开发票', '安装预约', '办理延保')
HUMAN_REQUEST = ('转人工', '找人工', '联系人工', '人工客服', '人工服务', '真人客服')
UNIT = re.compile(r'(?P<num>[+\-]?\d+(?:\.\d+)?)\s*(?P<unit>毫安时|千克|公斤|升|瓦|小时|分钟|秒钟|毫米|厘米|平方米|米|℃|kg|mah|mm|cm|m2|mpa|ghz|l|w|m|h)(?![a-z])', re.I)
UNIT_ALIAS = {'毫安时':'mah','千克':'kg','公斤':'kg','升':'l','瓦':'w','小时':'h','分钟':'分钟',
              '秒钟':'秒','毫米':'mm','厘米':'cm','平方米':'m2','米':'m','°c':'℃'}
OPERATION_CASES = [
    ('op_wifi_setup', ROBOT, '如何连接 Wi-Fi 并添加设备？', ('新增装置',), ('新增', '装置')),
    ('op_wifi_fault', ROBOT, '无法连接 Wi-Fi 时怎么排查密码和频段？', ('密码','2.4 GHz'), ('密码','2.4 GHz')),
    ('op_wifi_reset', ROBOT, '重设 Wi-Fi 要按多久？', ('7 秒',), ('7 秒', '重设')),
    ('op_charge_base', ROBOT, '充电座前方和左右各要留多少空位？', ('0.5 米','1.5 米'), ('0.5 米','1.5 米')),
    ('op_filter_dry', VAC, '洗过内置滤网后至少要晾晒多久？', ('24 小时',), ('内置滤网','24 小时')),
    ('op_remote', AC, '遥控器对准空调使用的距离是多少？', ('8 米',), ('遥控器','8 米')),
    ('op_outage', FRIDGE, '停电重新接通冰箱电源前至少等几分钟？', ('7 分钟',), ('至少保持间隔7 分钟',)),
    ('op_unlock', FRIDGE, '冰箱显示板被锁定时，如何按键解除锁定？', ('3 秒','解除锁定'), ('3 秒','解除锁定')),
    ('agg_robot', ROBOT, '扫拖机器人连接不了 Wi-Fi，手册有哪些排查步骤？', ('密码','2.4 GHz','覆盖范围','重设'), ('密码','2.4 GHz','覆盖范围','重设')),
    ('agg_clip', ROBOT, '首次准备扫拖机器人时要做哪些事？', ('防撞条','边刷','充电座','充电'), ('防撞条','边刷','充电座','充电')),
    ('agg_filter', VAC, '清理集尘桶时有哪三种配件要用清水冲洗？', ('前置过滤网','旋风系统组件','集尘桶'), ('前置过滤网','旋风系统组件','集尘桶')),
]
PARA_EVIDENCE = [('额定洗涤容量','9 kg'), ('产品净重','55 kg'), ('产品尺寸','850 mm'),
                 ('0.05 MPa',), ('32-48m²',), ('遥控器','8 米'), ('总容积','600L'),
                 ('2900 mAh',), ('4 小时',), ('43','温度')]
# Specific model IDs are supported by consecutive model/parameter blocks in one manual.
MODELS = [('BCD-600WGHSS17NZU1','600L'),('BCD-601WGHSS17B8U1','601L'),
          ('BCD-602WGHSS10B1U1','602L')]
DIALOGUES = [
    (WASH, [('额定洗涤容量是多少？','9 kg',False,WASH), ('产品净重是多少？','55 kg',False,WASH),
            ('那毛重呢？','59 kg',True,WASH), ('小米扫拖机器人 S20 的额定功率是多少？','45 W',False,ROBOT)]),
    (FRIDGE,[('总容积是多少？','600L',False,FRIDGE), ('那冷冻室呢？','195L',True,FRIDGE),
             ('额定功率是多少？','',False,FRIDGE), ('米家9kg洗衣机 WM090MJA08HK 的产品净重是多少？','55 kg',False,WASH)]),
    (ROBOT,[('额定功率是多少？','45 W',False,ROBOT), ('产品净重是多少？','3.4 kg',False,ROBOT),
            ('那它支持什么 Wi-Fi 频段？','2.4 GHz',True,ROBOT), ('额定电压是多少？','14.4 V',False,ROBOT)]),
    (AC,[('建议制冷面积是多少？','32-48m²',False,AC), ('遥控器有效距离是多少？','8 米',False,AC),
         ('那遥控器要对准哪里呢？','信号接收窗',True,AC), ('海尔冰箱 BCD-600WGHSS17NZU1 总容积是多少？','600L',False,FRIDGE)]),
    (VAC,[('首次充电需要多久？','4 小时',False,VAC), ('内置滤网需要晾晒多久？','24 小时',False,VAC),
          ('它的内置滤网呢，要晾晒多久？','24 小时',True,VAC), ('小米扫拖机器人 S20 的产品净重是多少？','3.4 kg',False,ROBOT)]),
]
ROUTE_CASES = [
    ('route_service_01', FRIDGE, '保修期内怎么申请上门维修？','handoff'),
    ('route_service_02', WASH, '请帮我预约安装，怎么做安装预约？','handoff'),
    ('route_service_03', VAC, '买错了，要申请换货。','handoff'),
    ('route_service_04', ROBOT, '我要开发票，怎么办？','handoff'),
    ('route_service_05', AC, '怎么办理延保？','handoff'),
    ('route_service_06', ROBOT, '我要退款。','handoff'),
    ('route_negative_01', ROBOT, '扫拖机器人工作声音有多大？','no_answer'),
    ('route_negative_02', ROBOT, '请问扫拖机器人支持卫星短信吗？','no_answer'),
    ('route_negative_03', WASH, '洗衣机能播放蓝光光盘吗？','no_answer'),
    ('route_negative_04', FRIDGE, '冰箱是否具有自动订机票功能？','no_answer'),
    ('route_param_01', WASH, '额定洗涤容量是多少？','answered'),
    ('route_param_02', FRIDGE, '总容积是多少？','answered'),
    ('route_param_03', ROBOT, '额定功率是多少？','answered'),
]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def case(case_id, product, question, expected=(), evidence=(), status='answered', category='诊断'):
    return {'id':case_id,'product_id':product,'version':'1.0','question':question,
            'expected_terms':list(expected),'expected_source':SOURCES[product].name if evidence else '',
            'evidence_terms':list(evidence),'expected_status':status,'category':category}


def unit_text(value):
    value = unicodedata.normalize('NFKC', value).casefold().replace('°c','℃')
    return UNIT.sub(lambda m: m['num'] + UNIT_ALIAS.get(m['unit'].casefold(),m['unit'].casefold()),value)


def answer_pass(case, row, equivalent=False):
    if row['status'] != case['expected_status'] or row['generation_error']:
        return False
    if case['expected_status'] != 'answered':
        return True
    if row['product_id'] != case['product_id'] or row['version'] != case['version']:
        return False
    if equivalent:
        return all(contains(unit_text(row['answer']),unit_text(t)) for t in case['expected_terms'])
    return all(t in row['answer'] for t in case['expected_terms'])


def rank(case, passages):
    labels = case['evidence_terms']
    if not labels:
        return None
    return next((i for i,p in enumerate(passages,1) if p['source']==case['expected_source']
                 and all(contains(p['text'],t) for t in labels)),0)


def project_answer(case, live):
    return {'status':live['status'],'answer':live['answer'],'product_id':live['product_id'],
            'version':live['version'],'generation_mode':live['generation_mode'],
            'generation_error':live['generation_error'],'rewritten':live['rewritten_question'],
            'retrieved':live['retrieved'],'sources':live['sources'],'confidence':live['confidence'],
            'rank':rank(case, live['retrieved'])}


def show_group(rows):
    out = {}
    for label in sorted({row['case']['category'] for row in rows}):
        out[label] = summarize([row for row in rows if row['case']['category']==label])
    return out


def summarize(rows):
    positive = [r for r in rows if r['case']['expected_status']=='answered']
    negative = [r for r in rows if r['case']['expected_status']=='no_answer']
    handoff = [r for r in rows if r['case']['expected_status']=='handoff']
    labeled = [r for r in rows if r['rank'] is not None]
    return {'n':len(rows),'strict':sum(r['strict'] for r in rows),'equivalent':sum(r['equivalent'] for r in rows),
            'status_ok':sum(r['status']==r['case']['expected_status'] and not r['generation_error'] for r in rows),
            'evidence_n':len(labeled),
            'hits':{str(k):sum(1<=r['rank']<=k for r in labeled) for k in (1,3,5)},
            'mrr_at5':round(sum((1/r['rank'] if r['rank'] else 0) for r in labeled)/len(labeled),4) if labeled else None,
            'positive_n':len(positive),'positive_refused':sum(r['status']=='no_answer' for r in positive),
            'negative_n':len(negative),'negative_refused':sum(r['status']=='no_answer' for r in negative),
            'handoff_n':len(handoff),'handoff_correct':sum(r['status']=='handoff' for r in handoff)}


@contextmanager
def settings(db_path, retriever='hash', threshold=None):
    env = {'RAG_RETRIEVER':retriever,'RAG_ANSWER_MODE':'extractive','RAG_LLM_DISABLED':'1',
           'EMBEDDING_PROVIDER':'local','EMBEDDING_MODEL':'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
           'RAG_MIN_SCORE':str(threshold if threshold is not None else (0.35 if retriever=='semantic' else 0.16))}
    with patch.object(core,'DB_PATH',db_path),patch.dict(os.environ,env):
        yield


def ask(case):
    return core.ask(case['question'],product_id=case['product_id'],version=case['version'],include_retrieved=True)


def run_cases(cases):
    rows = []
    for item in cases:
        row = project_answer(item,ask(item))
        rows.append({'case':item,**row,'strict':answer_pass(item,row),'equivalent':answer_pass(item,row,True)})
    return rows


def expand(question, product_id, version, top_k=4):
    terms = list(dict.fromkeys(term for phrase,term in MAPPINGS if phrase in question))
    expanded = ORIG_RETRIEVE(question+' '+ ' '.join(terms),product_id,version,top_k) if terms else []
    original = ORIG_RETRIEVE(question,product_id,version,top_k)
    # Keep the strongest match per original fragment.
    merged = {}
    for entry in original+expanded:
        key = entry['chunk_id']
        if key not in merged or merged[key]['score'] < entry['score']:
            merged[key] = entry
    return sorted(merged.values(),key=lambda row:row['score'],reverse=True)[:top_k]


def length_run(cases):
    out = []
    for item in cases:
        prelim = ORIG_RETRIEVE(item['question'],item['product_id'],item['version'],5)
        short = bool(prelim and len(prelim[0]['text'].split('\n',1)[-1])<=18)
        with patch.dict(os.environ,{'RAG_MIN_SCORE':'0.12' if short else '0.16'}):
            out.extend(run_cases([item]))
    return out


def snapshot(origin,target):
    with closing(sqlite3.connect(origin.resolve().as_uri()+'?mode=ro',uri=True)) as a, closing(sqlite3.connect(target)) as b:
        a.backup(b)
    with settings(target):
        core.init_db()


def rebuild(db_path):
    with settings(db_path):
        with core.connect() as db:
            sources = [dict(r) for r in db.execute('SELECT * FROM documents ORDER BY rowid')]
            db.execute('DELETE FROM documents')
        for row in sources:
            data = (ROOT/'说明书'/row['source']).read_bytes()
            core.add_document(row['product_id'],row['version'],row['category'],row['title'],row['source'],data,include_training=True)


def prep_cases():
    primary = read(AUTO)
    for x in primary:
        x['evidence_terms'] = ([x['expected_evidence']] if x['expected_status']=='answered'
                               and x.get('expected_evidence') else [])
    paraphrase = read(PARAPHRASES)
    for i,(x,terms) in enumerate(zip(paraphrase,PARA_EVIDENCE),1):
        x.update(id=f'para_{i:02}',expected_source=SOURCES[x['product_id']].name,evidence_terms=list(terms))
    operations = [case(i,p,q,terms,evidence,category='聚合' if i.startswith('agg_') else '操作/故障')
                  for i,p,q,terms,evidence in OPERATION_CASES]
    return primary,paraphrase,operations


def contextual_coverage(cases):
    rows = []
    for item in cases:
        pool = ORIG_RETRIEVE(item['question'],item['product_id'],item['version'],top_k=8)
        top = pool[0] if pool else None
        out = {}
        for k in (3,5,8):
            doc_only = [p['text'] for p in pool[:k] if p['source']==item['expected_source']]
            out[str(k)] = [t for t in item['evidence_terms'] if any(contains(text,t) for text in doc_only)]
        rows.append({'case':item,'top_score':top['score'] if top else None,'coverage':out})
    return rows


def covered(rows,k):
    return sum(set(x['coverage'][str(k)])==set(x['case']['evidence_terms']) for x in rows)


def model_check():
    text = SOURCES[FRIDGE].read_text(encoding='utf-8')
    blocks = {}
    for block in re.split(r'\n\s*\n', text):
        model = re.match(r'^型号[：:] *(BCD-[A-Z\d]+)', block)
        if model and '\n总容积' in block:
            blocks.setdefault(model.group(1), block)
    rows = []
    for model,value in MODELS:
        x=case(model,FRIDGE,f'{model} 冰箱的总容积是多少？',(value,),('总容积',value))
        app=run_cases([x])[0]
        block=blocks[model]
        raw=re.search(r'^总容积[：:]\s*([^\n]+)',block,re.M).group(1)
        rows.append({'case':x,'baseline':app,'prototype_answer':raw,'prototype_passed':unit_text(raw)==unit_text(value),
                     'model_block':block})
    return rows


def dialogue_checks():
    results = []
    for index,(product,turns) in enumerate(DIALOGUES,1):
        sid = None
        for turn,(question,answer,followup,scope) in enumerate(turns,1):
            r = core.ask(question,sid,product_id=product if sid is None else None,
                         version='1.0' if sid is None else None,include_retrieved=True)
            sid = r['session_id']
            rewritten = r['rewritten_question']!=question
            results.append({'dialogue':index,'turn':turn,'question':question,'answer_label':answer,'followup_label':followup,
                            'rewritten':r['rewritten_question'],'triggered':rewritten,'actual_scope':r['product_id'],
                            'expected_scope':scope,'scope_ok':r['product_id']==scope,'status':r['status'],'answer':r['answer'],
                            'correct':(r['status']=='answered' and r['product_id']==scope and contains(unit_text(r['answer']),unit_text(answer))) if answer else None})
    return results


def archived_scores(cases):
    expected = {case['id']: case for case in cases}
    rows = []
    for original in read(ARCHIVED_LLM)['rows']:
        item = expected[original['id']]
        status_ok = original['actual_status'] == item['expected_status']
        terms = item['expected_terms'] if item['expected_status']=='answered' else []
        answer = original['answer']
        strict = status_ok and all(term in answer for term in terms)
        equivalent = status_ok and all(contains(unit_text(answer),unit_text(term)) for term in terms)
        rows.append({'id':item['id'],'expected_status':item['expected_status'],'actual_status':original['actual_status'],
                     'expected_terms': terms,'answer':answer,'strict':strict,'equivalent':equivalent})
    assert len(rows)==len(cases)
    p = [r for r in rows if r['expected_status']=='answered']
    return {'total':len(rows),'strict_all':sum(r['strict'] for r in rows),
            'equivalent_all':sum(r['equivalent'] for r in rows),
            'positive_n':len(p),'strict_positive':sum(r['strict'] for r in p),
            'equivalent_positive':sum(r['equivalent'] for r in p),
            'rescued':[r for r in rows if r['equivalent'] and not r['strict']],
            'lost':[r for r in rows if r['strict'] and not r['equivalent']]}


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--semantic',action='store_true')
    args.add_argument('--output',type=Path,default=Path(__file__).with_name('离线实测.json'))
    opts = args.parse_args()
    primary, para, ops = prep_cases()
    routes = [case(i,p,q,status=s,category='路由') for i,p,q,s in ROUTE_CASES]
    result = {'code_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'input_sha256':{str(p.relative_to(ROOT)):checksum(p) for p in (SNAPSHOT,AUTO,PARAPHRASES,ARCHIVED_LLM,*SOURCES.values())},
              'read_only_source':'data/sms.sqlite3','notes':[
                  'CHAM-QA v7.1 70/179 and 15-product snapshot unavailable; separate local diagnostic sets.',
                  'Gold labels in repository auto set are weak; no cross-variant tuning.',
                  'Strict uses literal substring, equivalent uses NFKC/case/space guard and listed unit aliases only.',
                  'An evidence match is label passage match, not proof of answer truth; negative explanations and handoff markers are excluded from retrieval denominators.']}
    with closing(sqlite3.connect(SNAPSHOT.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        result['input_counts']={table:conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                                for table in ('products','documents','chunks','semantic_vectors')}
        result['archived_llm_scoring']=archived_scores(primary)
        result['stored_vectors']=conn.execute('SELECT identity,dimension,count(*) FROM semantic_vectors GROUP BY identity,dimension').fetchall()
    with tempfile.TemporaryDirectory(prefix='rag-improvement-') as temp:
        temp=Path(temp)
        baseline=temp/'baseline.sqlite3'
        snapshot(SNAPSHOT,baseline)
        with settings(baseline):
            all_rows=run_cases(primary+para+ops)
            result['baseline']={'auto':all_rows[:len(primary)],'paraphrase':all_rows[len(primary):len(primary)+len(para)],
                                'ops':all_rows[-len(ops):]}
            result['context_baseline']=contextual_coverage(ops)
            result['route_baseline']=run_cases(routes)
            result['models']=model_check()
            result['dialogue_baseline']=dialogue_checks()
            with patch.object(core,'FOLLOWUP_WORDS',NARROW):
                result['dialogue_narrow']=dialogue_checks()
            base_words = core.HANDOFF_WORDS
            with patch.object(core,'HANDOFF_WORDS',base_words+HANDOFF_EXTRA):
                result['route_action_phrases']=run_cases(routes)
            with patch.object(core,'HANDOFF_WORDS',tuple(t for t in base_words if t!='人工')+HUMAN_REQUEST+HANDOFF_EXTRA):
                result['route_explicit_human']=run_cases(routes)
            with patch.object(core,'retrieve',expand):
                result['mapping']={'auto':run_cases(primary),'paraphrase':run_cases(para)}
            result['short_threshold']={'auto':length_run(primary),'paraphrase':length_run(para)}
            with patch.dict(os.environ,{'RAG_MIN_SCORE':'0.16'}):
                result['fixed_scope_english_gate']=[{
                    'id':j['case']['id'],'top':j['retrieved'][0] if j['retrieved'] else None,
                    'actual_status':j['status']} for j in result['baseline']['auto'] if j['case']['expected_status']=='answered'
                    and j['status']=='no_answer' and j['rank'] and j['rank']>0]
        print('char, route, dialogue complete',flush=True)
        newer=temp/'reimport.sqlite3'
        snapshot(SNAPSHOT,newer)
        rebuild(newer)
        with settings(newer):
            with core.connect() as conn:
                result['reimport_counts']={k:conn.execute(f'SELECT count(*) FROM {k}').fetchone()[0] for k in ('documents','chunks','faqs')}
            result['reimport']={'auto':run_cases(primary),'ops':run_cases(ops)}
            result['context_reimport']=contextual_coverage(ops)
        print('reimport complete',flush=True)
        if opts.semantic:
            db=temp/'semantic.sqlite3'
            snapshot(SNAPSHOT,db)
            with settings(db,'semantic'):
                result['semantic_index']=semantic.index_chunks(core.connect)
                result['semantic']={'auto':run_cases(primary),'paraphrase':run_cases(para),'ops':run_cases(ops)}
            print('semantic complete',flush=True)
    result['counts']={label:{group:summarize(rows) for group,rows in result[label].items()}
                      for label in ('baseline','mapping','short_threshold','reimport','semantic') if label in result}
    result['counts_by_category']={label:{group:show_group(rows) for group,rows in result[label].items()}
                                  for label in result['counts']}
    result['route_counts']={label:show_group([{**row,'case':{**row['case'],'category':row['case']['expected_status']}}
                                            for row in result[label]]) for label in ('route_baseline','route_action_phrases','route_explicit_human')}
    result['context_counts']={label:{str(k):covered(result[label],k) for k in (3,5,8)}
                              for label in ('context_baseline','context_reimport')}
    result['dialogue_counts']={}
    for key in ('dialogue_baseline','dialogue_narrow'):
        rows=result[key]
        independent=[r for r in rows if r['turn']>1 and not r['followup_label']]
        follow=[r for r in rows if r['followup_label']]
        switches=[r for r in rows if r['turn']==4 and r['expected_scope']!=DIALOGUES[r['dialogue']-1][0]]
        result['dialogue_counts'][key]={'turns':len(rows),'independent_n':len(independent),
            'false_trigger':sum(r['triggered'] for r in independent),'followup_n':len(follow),
            'followup_triggered':sum(r['triggered'] for r in follow),'followup_answer_correct':sum(r['correct'] is True for r in follow),
            'labeled_answers':sum(r['correct'] is not None for r in rows),'answer_correct':sum(r['correct'] is True for r in rows),
            'switch_scope_correct':sum(r['scope_ok'] for r in switches),'switch_n':len(switches)}
    failure=collections.Counter()
    for row in result['baseline']['auto']:
        if row['strict']:
            continue
        if row['equivalent']:
            reason='unit_or_format_candidate'
        elif row['case']['expected_status']!='answered':
            reason='route_or_abstention'
        elif row['rank']==0:
            reason='gold_outside_top5'
        elif row['rank'] is None:
            reason='no_evidence_label'
        elif row['status']=='no_answer':
            reason='abstained_with_gold_in_top5'
        else:
            reason='answer_or_rank_mismatch'
        row['failure_bucket']=reason
        failure[reason]+=1
    result['baseline_failures']=dict(failure)
    opts.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('counts','route_counts','dialogue_counts','context_counts','baseline_failures')},ensure_ascii=False))


if __name__=='__main__':
    main()
