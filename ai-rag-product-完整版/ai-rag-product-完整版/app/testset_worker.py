"""Score managed test cases on a private knowledge snapshot."""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
import unicodedata
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import sqlite3

from . import core, experiments


FAILURE_LABELS = {
    'scope_missing': '产品版本没有资料',
    'scope_changed': '实际回答范围与预期不同',
    'execution_error': '运行异常',
    'generation_error': '模型生成失败',
    'false_acceptance': '应拒答但给出了答案',
    'false_refusal': '有答案却拒答',
    'status_mismatch': '回答状态不符',
    'retrieval_miss': 'Top 5 未检索到标注证据',
    'retrieval_ranking': '标注证据未排在首位',
    'answer_mismatch': '答案未达到标注要求',
    'citation_mismatch': '回答引用与标注证据不符',
}

SCORING_VERSION = 'literal-and-units-v1'
UNITS = {
    '毫安时': 'mah', '千克': 'kg', '公斤': 'kg', '克': 'g', '升': 'l', '瓦': 'w',
    '伏': 'v', '安培': 'a', '小时': 'h', '分钟': 'min', '秒钟': 's', '秒': 's',
    '毫米': 'mm', '厘米': 'cm', '平方米': 'm2', '米': 'm', '摄氏度': '°c',
}
MEASUREMENT = re.compile(
    r'(?<![a-z0-9.])(?P<signed>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>'
    + '|'.join(sorted(set(UNITS) | set(UNITS.values()), key=lambda value: (-len(value), value)))
    + r')(?![a-z/])'
)


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text).casefold())


def contains(text, expected):
    term = re.escape(normalized(expected))
    if not term:
        return False
    if term[0].isdigit():
        term = r'(?<!\d)(?<!\d\.)' + term
    if term[-1].isdigit():
        term += r'(?!\d|\.\d)'
    return bool(re.search(term, normalized(text)))


def normalize_units(text):
    text = unicodedata.normalize('NFKC', text).casefold()

    def measurement(match):
        value = Decimal(match['signed'])
        number = format(value, 'f')
        if '.' in number:
            number = number.rstrip('0').rstrip('.')
        return number + UNITS.get(match['unit'], match['unit'])

    return MEASUREMENT.sub(measurement, text)


def answer_scores(case, answer, status_ok):
    if case['expected_status'] != 'answered':
        credit = 100 if status_ok else 0
        return credit, credit, []
    terms = case['expected_terms'] or [case['expected_answer']]
    missing_strict = [term for term in terms if term not in answer]
    equivalent = normalize_units(answer)
    measurements = {item[0] for item in MEASUREMENT.finditer(equivalent)}
    missing = []
    for term in terms:
        expected = normalize_units(term).strip()
        matched = expected in measurements if MEASUREMENT.fullmatch(expected) else contains(equivalent, expected)
        if not matched:
            missing.append(term)
    if not status_ok:
        scores = 0, 0
    else:
        scores = tuple(round(100 * (1 - len(missed) / len(terms)), 2) for missed in (missing_strict, missing))
    return scores[0], scores[1], missing


def matching_evidence(case, candidates):
    return [
        n for n, source in enumerate(candidates, 1)
        if (not case['expected_source'] or normalized(source['source']) == normalized(case['expected_source']))
        and (not case['expected_evidence'] or contains(source['text'], case['expected_evidence']))
    ]


def score_case(case, result, latency):
    actual_status = result['status']
    actual_answer = result.get('answer', '')
    generation_error = result.get('generation_error')
    retrieved, sources = result.get('retrieved', []), result.get('sources', [])
    answerable = case['expected_status'] == 'answered'
    evidence_labeled = bool(case['expected_source'] or case['expected_evidence'])
    ranks = matching_evidence(case, retrieved) if evidence_labeled else []
    rank = ranks[0] if ranks else None
    citation_ok = bool(matching_evidence(case, sources)) if evidence_labeled and actual_status == 'answered' else None
    scope_ok = not case['product_id'] or not answerable or (
        result.get('product_id') == case['product_id'] and result.get('version') == case['version'])
    status_ok = actual_status == case['expected_status'] and not generation_error and scope_ok
    strict_score, score, missing_terms = answer_scores(case, actual_answer, status_ok)

    reasons = []
    if result.get('failure'):
        reasons.append(result['failure'])
    elif generation_error:
        reasons.append('generation_error')
    elif not scope_ok:
        reasons.append('scope_changed')
    elif not status_ok:
        if actual_status == 'answered' and case['expected_status'] in ('no_answer', 'no_product'):
            reasons.append('false_acceptance')
        elif answerable and actual_status in ('no_answer', 'no_product'):
            reasons.append('false_refusal')
        else:
            reasons.append('status_mismatch')
    if evidence_labeled:
        if rank is None:
            reasons.append('retrieval_miss')
        elif rank > 1:
            reasons.append('retrieval_ranking')
    if status_ok and score < 100:
        reasons.append('answer_mismatch')
    if citation_ok is False:
        reasons.append('citation_mismatch')
    return {
        'case': case, 'actual_status': actual_status, 'actual_answer': actual_answer,
        'actual_product_id': result.get('product_id'), 'actual_version': result.get('version'),
        'generation_mode': result.get('generation_mode'), 'generation_error': generation_error,
        'model_used': result.get('model_used'),
        'retrieval_candidates': result.get('retrieval_candidates'), 'retrieval_fallback': result.get('retrieval_fallback'),
        'latency_ms': latency, 'score': score, 'strict_score': strict_score, 'status_ok': status_ok,
        'passed': status_ok and score == 100 and citation_ok is not False,
        'strict_passed': status_ok and strict_score == 100 and citation_ok is not False,
        'missing_terms': missing_terms, 'sources': sources, 'retrieved': retrieved,
        'evidence_rank': rank, 'evidence_labeled': evidence_labeled, 'citation_ok': citation_ok,
        'reasons': reasons,
    }


def execute_case(case, scopes):
    started = time.monotonic()
    if case['product_id'] and (case['product_id'], case['version']) not in scopes:
        result = {'status': 'error', 'failure': 'scope_missing'}
    else:
        session = None
        try:
            scope = (case['product_id'] or None, case['version'] or None)
            for previous in case['history']:
                session = core.ask(previous, session, *scope)['session_id']
            result = core.ask(case['question'], session, *scope, include_retrieved=True)
        except Exception as error:
            result = {'status': 'error', 'failure': 'execution_error', 'generation_error': type(error).__name__}
    return score_case(case, result, round((time.monotonic() - started) * 1000, 1))


def summarize(rows):
    def mean(values):
        return round(statistics.mean(values), 4) if values else None

    evidence = [r for r in rows if r['evidence_labeled']]
    citations = [r['citation_ok'] for r in rows if r['citation_ok'] is not None]
    refusal = [r['status_ok'] for r in rows if r['case']['expected_status'] == 'no_answer']
    reason_counts, observed_models = {}, {}
    for row in rows:
        for reason in row['reasons']:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if row['model_used']:
            model = row['model_used']
            observed_models[model] = observed_models.get(model, 0) + 1
    return {
        'total': len(rows), 'passed': sum(row['passed'] for row in rows),
        'strict_passed': sum(row['strict_passed'] for row in rows), 'scoring_version': SCORING_VERSION,
        'status_accuracy': mean([r['status_ok'] for r in rows]),
        'average_score': mean([r['score'] for r in rows]),
        'mean_latency_ms': mean([r['latency_ms'] for r in rows]),
        'refusal_accuracy': mean(refusal), 'evidence_cases': len(evidence),
        'hit_at_k': {str(k): mean([bool(r['evidence_rank'] and r['evidence_rank'] <= k) for r in evidence])
                     for k in (1, 3, 5)},
        'mrr_at_5': mean([1 / r['evidence_rank'] if r['evidence_rank'] else 0 for r in evidence]),
        'citation_accuracy': mean(citations), 'reason_counts': reason_counts, 'observed_models': observed_models,
    }


def compare_rows(baseline, candidate):
    before = {row['case']['id']: row for row in baseline}
    after = {row['case']['id']: row for row in candidate}
    changes = {key: [] for key in ('regressions', 'improvements', 'score_drops', 'score_gains',
                                   'retrieval_drops', 'new', 'removed', 'edited')}
    comparable = 0
    same_scoring = all('strict_score' in row for row in baseline + candidate)

    def score(value):
        if same_scoring:
            return value['score']
        return answer_scores(value['case'], value['actual_answer'], value['status_ok'])[0]

    def passed(value):
        if same_scoring:
            return value['passed']
        return value['status_ok'] and score(value) == 100 and value['citation_ok'] is not False

    for case_id, row in after.items():
        old = before.get(case_id)
        case = row['case']
        item = {'case_id': case_id, 'question': case['question'], 'before_score': score(old) if old else None,
                'after_score': score(row), 'before_rank': old['evidence_rank'] if old else None,
                'after_rank': row['evidence_rank']}
        if old is None:
            changes['new'].append(item)
            continue
        if experiments.dataset_digest([old['case']]) != experiments.dataset_digest([case]):
            changes['edited'].append(item)
            continue
        comparable += 1
        if passed(old) and not passed(row):
            changes['regressions'].append(item)
        elif not passed(old) and passed(row):
            changes['improvements'].append(item)
        if item['after_score'] < item['before_score']:
            changes['score_drops'].append(item)
        elif item['after_score'] > item['before_score']:
            changes['score_gains'].append(item)
        if old['evidence_rank'] and (not row['evidence_rank'] or row['evidence_rank'] > old['evidence_rank']):
            changes['retrieval_drops'].append(item)
    changes['removed'] = [{'case_id': case_id, 'question': row['case']['question']}
                          for case_id, row in before.items() if case_id not in after]
    return {'comparable': comparable, 'comparison_scoring': 'normalized_units' if same_scoring else 'literal', **changes}


def run(payload):
    original = Path(os.environ['RAG_DB'])
    reports = []
    for index, variant in enumerate(payload['variants']):
        working_db = original.with_name(f'variant-{index}.sqlite3')
        with closing(sqlite3.connect(original)) as source, closing(sqlite3.connect(working_db)) as target:
            source.backup(target)
        core.DB_PATH = working_db
        os.environ.update(RAG_RETRIEVER=variant['retriever'], RAG_ANSWER_MODE=variant['answer_mode'],
                          RAG_MIN_SCORE=str(variant['threshold']),
                          RAG_LLM_DISABLED='0' if variant['answer_mode'] == 'llm' else '1')
        if variant['answer_mode'] == 'llm':
            os.environ.update(LLM_BASE_URL=payload['llm']['base_url'], LLM_API_KEY=payload['llm']['api_key'],
                              LLM_MODEL=variant['model'])
        with closing(core.connect()) as db:
            scopes = {tuple(row) for row in db.execute('SELECT DISTINCT product_id,version FROM documents')}
        rows = [execute_case(case, scopes) for case in payload['cases']]
        reports.append({'variant': variant, 'metrics': summarize(rows), 'rows': rows})
    return reports


if __name__ == '__main__':
    print(json.dumps(run(json.load(sys.stdin)), ensure_ascii=False, allow_nan=False))
