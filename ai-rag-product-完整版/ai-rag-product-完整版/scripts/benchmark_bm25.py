"""Compare warm retrieval latency and answer quality on the shipped manuals."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from statistics import median
import sys
import tempfile
from time import perf_counter_ns
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import bm25, core
from app.testset_worker import execute_case, summarize

MODES = ('hash', 'bm25')


def read_cases(path):
    cases = json.loads(path.read_text(encoding='utf-8'))
    for case in cases:
        case.setdefault('expected_answer', '')
        case.setdefault('history', [])
        # Negative examples contain explanatory annotations, not gold passages.
        if case['expected_status'] != 'answered':
            case['expected_evidence'] = ''
            case['expected_source'] = ''
    return cases


def timing(cases, repeats):
    durations = {mode: [] for mode in MODES}
    candidate_counts = {mode: [] for mode in MODES}
    fallbacks = 0
    searchable = [case for case in cases if case['expected_status'] != 'handoff']
    for repeat in range(repeats):
        for index, case in enumerate(searchable):
            order = MODES if (repeat + index) % 2 else MODES[::-1]
            for mode in order:
                with patch.dict(os.environ, {'RAG_RETRIEVER': mode}):
                    details = {}
                    started = perf_counter_ns()
                    core.retrieve(case['question'], case['product_id'], case['version'], top_k=5, details=details)
                    durations[mode].append((perf_counter_ns() - started) / 1e6)
                    if 'retrieval_candidates' in details:
                        candidate_counts[mode].append(details['retrieval_candidates'])
                    fallbacks += bool(details.get('retrieval_fallback'))
    summary = {}
    for mode, samples in durations.items():
        samples.sort()
        summary[mode] = {
            'queries': len(samples), 'p50_ms': round(median(samples), 3),
            'p95_ms': round(samples[(95 * len(samples) + 99) // 100 - 1], 3),
            'median_rerank_candidates': median(candidate_counts[mode]) if candidate_counts[mode] else None,
            'fallback_queries': fallbacks if mode == 'bm25' else 0,
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=6)
    parser.add_argument('--output', type=Path, default=Path('reports/bm25-comparison.json'))
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    source = ROOT / 'data/sms.sqlite3'
    cases_file = ROOT / 'data/eval_auto.json'
    cases = read_cases(cases_file)
    report = {
        'sqlite': sqlite3.sqlite_version,
        'snapshot_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'cases_sha256': hashlib.sha256(cases_file.read_bytes()).hexdigest(),
        'settings': {'answer_mode': 'extractive', 'threshold': 0.16, 'bm25_candidates': bm25.CANDIDATE_LIMIT},
        'quality': {},
    }
    with tempfile.TemporaryDirectory(prefix='rag-bm25-') as folder:
        snapshot = Path(folder) / 'manuals.sqlite3'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original,                 closing(sqlite3.connect(snapshot)) as copy:
            original.backup(copy)
        with patch.object(core, 'DB_PATH', snapshot), patch.dict(os.environ, {
            'RAG_ANSWER_MODE': 'extractive', 'RAG_LLM_DISABLED': '1', 'RAG_MIN_SCORE': '0.16',
        }):
            core.init_db()
            with closing(core.connect()) as db, db:
                if not bm25.available(db):
                    parser.exit(1, 'SQLite FTS5 is required for this benchmark.\n')
                scopes = {tuple(row) for row in db.execute('SELECT DISTINCT product_id,version FROM documents')}
                started = perf_counter_ns()
                bm25.refresh(db)
                db.commit()
                report['index_build_ms'] = round((perf_counter_ns() - started) / 1e6, 3)
                report['indexed_chunks'] = db.execute('SELECT count(*) FROM rag_bm25').fetchone()[0]
            for mode in MODES:
                with patch.dict(os.environ, {'RAG_RETRIEVER': mode}):
                    rows = [execute_case(case, scopes) for case in cases]
                    report['quality'][mode] = summarize(rows)
            report['warm_retrieval'] = timing(cases, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
