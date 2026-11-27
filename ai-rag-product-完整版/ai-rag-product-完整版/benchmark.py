"""Compare retrieval baselines on a fresh corpus, exporting evidence for review."""
import argparse
import csv
import json
import os
import platform
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app import chroma_index, core, experiments, llm_config, postgres, semantic, snapshots


def write_report(report, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'benchmark.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (output / 'human_review.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        fields = ['run', 'case_id', 'record_digest', 'question', 'expected_status', 'expected_terms',
                  'actual_status', 'actual_answer', 'evidence', 'generation_mode',
                  'faithfulness', 'answer_relevance', 'reviewer', 'reason']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for run, result in report['runs'].items():
            for row in result['comparisons']:
                writer.writerow({'run': run, 'case_id': row['id'],
                    'record_digest': experiments.dataset_digest([row]),
                    'question': row['question'], 'expected_status': row['expected_status'],
                    'expected_terms': ' / '.join(row.get('expected_terms', [])),
                    'actual_status': row['actual_status'], 'actual_answer': row['actual_answer'],
                    'evidence': json.dumps(row['sources'], ensure_ascii=False),
                    'generation_mode': row['generation_mode']})
    lines = ['# 实验结果', '', '本文件由 benchmark.py 根据实际运行结果生成。关键词匹配不等同于忠实度。', '',
             '| 方案 | 通过/总数 | 状态准确率 | 答案关键词准确率 | Hit@1（证据片段） | LLM 回答数 |',
             '| --- | --- | --- | --- | --- | --- |']
    for run, r in report['runs'].items():
        lines.append(f"| {run} | {r['passed']}/{r['cases']} | {r['status_accuracy']} | {r['answer_keyword_accuracy']} | {r['hit_at_k']['1']} | {r['llm_answers']} |")
    lines += ['', '忠实度与答案相关性：待人工评阅 human_review.csv，空白不计为零分或满分。', '',
              '## 未执行方案', '', *[f'- {k}：{v}' for k, v in report['skipped'].items()], '',
              '## 实际失败案例', '']
    for run, r in report['runs'].items():
        for row in r['comparisons']:
            if not row['passed']:
                lines += [f"### {run} / {row['id']}", '', f"问题：{row['question']}",
                          f"预期：{row['expected_status']} / {'、'.join(row.get('expected_terms', []))}",
                          f"实际：{row['actual_status']} / {row['actual_answer']}",
                          f"证据检索 Hit@1：{row['hit_at_k']['1']}；生成方式：{row['generation_mode']}", '']
    (output / 'RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


@contextmanager
def experiment_database(use_chroma=False):
    if postgres.enabled():
        with snapshots.schema_snapshot(copy_knowledge=False) as schema:
            with patch.dict(os.environ, {'RAG_POSTGRES_SCHEMA': schema}):
                try:
                    yield
                finally:
                    if use_chroma and chroma_index.configured():
                        try:
                            chroma_index.delete_collection()
                        except Exception:
                            print('无法清理本次评测的 Chroma 集合。')
    else:
        with tempfile.TemporaryDirectory(prefix='rag-benchmark-') as temp:
            with patch.object(core, 'DB_PATH', Path(temp) / 'experiment.sqlite3'):
                yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrievers', nargs='+', choices=['hash', 'bm25', 'semantic', 'chroma', 'hybrid'], default=['hash', 'semantic'])
    parser.add_argument('--with-llm', action='store_true', help='Include strict LLM runs; never silently fall back')
    parser.add_argument('--output', type=Path, default=Path('reports/latest'))
    args = parser.parse_args()
    cases = json.loads((core.ROOT / 'data/eval_extended.json').read_text(encoding='utf-8'))
    corpus = experiments.load_corpus()
    model_config = llm_config.effective(core.llm_settings_path())
    model_ready = llm_config.enabled(core.llm_settings_path())
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
              'dataset_sha256': experiments.dataset_digest(cases),
              'corpus_sha256': experiments.dataset_digest(corpus),
              'corpus_documents': len(corpus), 'corpus_characters': sum(len(d['text']) for d in corpus),
              'fictional_corpus': True, 'embedding_model': semantic.config()[1],
              'llm_model': model_config['model'] if args.with_llm and model_ready else None,
              'runs': {}, 'skipped': {}}
    with experiment_database(use_chroma=bool({'chroma', 'hybrid'} & set(args.retrievers))):
        experiments.seed_experiment()
        with core.connect() as db:
            report['chunks'] = db.execute('SELECT count(*) FROM chunks').fetchone()[0]
            report['faqs'] = db.execute('SELECT count(*) FROM faqs').fetchone()[0]
            report['cleaning_reports'] = [json.loads(r[0]) for r in db.execute('SELECT cleaning_report FROM documents')]
        for retriever in args.retrievers:
            if retriever == 'bm25' and not postgres.enabled():
                with core.connect() as db:
                    if not core.bm25.available(db):
                        report['skipped']['bm25'] = '当前 SQLite 未启用 FTS5'
                        continue
                    core.bm25.refresh(db)
            if retriever in ('chroma', 'hybrid'):
                try:
                    chroma_index.reindex(core.connect)
                except Exception as error:
                    report['skipped'][retriever] = type(error).__name__ + '：Chroma 或 Embedding 服务不可用'
                    continue
            if retriever == 'semantic':
                try:
                    import faiss
                    semantic.index_chunks(core.connect)
                except Exception as error:
                    report['skipped']['semantic'] = type(error).__name__ + '：语义依赖、模型或服务不可用'
                    continue
            for mode in (['extractive', 'llm'] if args.with_llm else ['extractive']):
                name = retriever + '_' + mode
                if mode == 'llm' and not model_ready:
                    report['skipped'][name] = '未配置完整 LLM 服务，未执行真实模型实验'
                    continue
                # Thresholds are explicitly fixed before evaluating; no tuning on this set.
                threshold = '0.35' if retriever in ('semantic', 'chroma') else '0.16'
                environment = {'RAG_RETRIEVER': retriever, 'RAG_ANSWER_MODE': mode, 'RAG_MIN_SCORE': threshold}
                if mode == 'llm':
                    environment.update(LLM_BASE_URL=model_config['base_url'], LLM_API_KEY=model_config['api_key'],
                                       LLM_MODEL=model_config['model'])
                with patch.dict(os.environ, environment):
                    print('running', name, flush=True)
                    report['runs'][name] = experiments.run_cases(cases)
                    report['runs'][name]['threshold'] = float(threshold)
        if not args.with_llm:
            report['skipped']['llm'] = '未请求真实模型实验；配置服务后加 --with-llm'
    write_report(report, args.output)
    print(json.dumps({k: {'cases': v['cases'], 'passed': v['passed']} for k, v in report['runs'].items()}, ensure_ascii=False))
    print('saved:', args.output)


if __name__ == '__main__':
    main()
