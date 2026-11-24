"""Replay the existing local evaluation labels against the current application."""
import argparse
import json
import os
from hashlib import sha256
from pathlib import Path
import tempfile

import 复现实验 as diagnostics


def dialogue_metrics(rows):
    independent = [r for r in rows if r['turn'] > 1 and not r['followup_label']]
    followups = [r for r in rows if r['followup_label']]
    return {
        'independent': len(independent), 'false_triggers': sum(r['triggered'] for r in independent),
        'followups': len(followups), 'correct_followups': sum(r['correct'] is True for r in followups),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--semantic', action='store_true', help='Requires FAISS and the cached multilingual MiniLM model')
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('优化回归.json'))
    args = parser.parse_args()
    baseline = diagnostics.read(Path(__file__).with_name('离线实测.json'))
    prompt_baseline = diagnostics.read(Path(__file__).with_name('提示词逐题结果.json'))
    digest = diagnostics.checksum(diagnostics.SNAPSHOT)
    if digest != baseline['input_sha256']['data/sms.sqlite3']:
        raise ValueError('当前说明书快照与原评估不一致')
    for filename, fingerprint in baseline['input_sha256'].items():
        if diagnostics.checksum(diagnostics.ROOT / filename) != fingerprint:
            raise ValueError(f'评估输入已变更：{filename}')

    old_routes = diagnostics.summarize(baseline['route_baseline'])
    old_models = diagnostics.summarize([item['baseline'] for item in baseline['models']])
    old_dialogue = dialogue_metrics(baseline['dialogue_baseline'])
    primary, paraphrases, operations = diagnostics.prep_cases()
    routes = [diagnostics.case(i, p, q, status=s, category='路由') for i,p,q,s in diagnostics.ROUTE_CASES]
    models = [row['case'] for row in baseline['models']]
    prompt_hash = sha256(diagnostics.core.default_answer_prompt().strip().encode()).hexdigest()
    output = {
        'scope': '现有5款家电的本地诊断回归。口语与专项题参与过开发验证。缺少CHAM-QA v7.1冻结题集和对应语料。',
        'source_code_sha256': {name: diagnostics.checksum(diagnostics.ROOT / name) for name in (
            'app/core.py', 'app/query.py', 'app/semantic.py', 'app/testset_worker.py', 'prompts/answer.txt')},
        'input_sha256': baseline['input_sha256'],
        'before': {'hash': baseline['counts']['baseline'], 'semantic': baseline['counts']['semantic'],
                   'routes': old_routes, 'models': old_models, 'dialogues': old_dialogue},
        'after': {},
        'default_prompt': {'sha256': prompt_hash, 'same_as_evaluated_group_b': prompt_hash == prompt_baseline['prompt_sha256']['B'],
                           'testing_model': prompt_baseline['model'], 'group_A': prompt_baseline['counts']['A'],
                           'group_B': prompt_baseline['counts']['B']},
    }
    if args.semantic:
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
    with tempfile.TemporaryDirectory(prefix='rag-optimization-replay-') as temp:
        folder = Path(temp)
        for mode in (('hash', 'semantic') if args.semantic else ('hash',)):
            database = folder / (mode+'.sqlite3')
            diagnostics.snapshot(diagnostics.SNAPSHOT,database)
            with diagnostics.settings(database,mode):
                target = {name: diagnostics.summarize(diagnostics.run_cases(dataset)) for name,dataset in {
                    'auto':primary, 'paraphrase':paraphrases, 'ops':operations, 'routes':routes, 'models':models}.items()}
                target['dialogues'] = dialogue_metrics(diagnostics.dialogue_checks())
                output['after'][mode] = target
        database = folder/'reimport.sqlite3'
        diagnostics.snapshot(diagnostics.SNAPSHOT,database)
        diagnostics.rebuild(database)
        with diagnostics.settings(database):
            output['after']['reimported_operations'] = diagnostics.summarize(diagnostics.run_cases(operations))
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
