"""Run a transparent, small smoke evaluation on a fresh temporary database."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
from app import postgres, snapshots


def run():
    if postgres.enabled():
        with snapshots.schema_snapshot(copy_knowledge=False) as schema:
            return run_cases(snapshots.environment(schema))
    with tempfile.TemporaryDirectory() as temp:
        return run_cases({**os.environ, 'RAG_DB': str(Path(temp) / 'eval.sqlite3')})


def run_cases(environment):
    environment.update(RAG_LLM_DISABLED='1', RAG_ANSWER_MODE='extractive',
                       RAG_RETRIEVER=os.getenv('RAG_RETRIEVER', 'hash'))
    environment.pop('LLM_BASE_URL', None)
    subprocess.run([sys.executable, str(ROOT / 'seed.py')], check=True,
                   env=environment, stdout=subprocess.DEVNULL)
    process = subprocess.run([sys.executable, '-m', 'app.evaluation_worker'],
                             env=environment, check=True, capture_output=True, text=True, cwd=ROOT)
    report = json.loads(process.stdout)
    for row in report['comparisons']:
        print('PASS' if row['passed'] else 'FAIL', row['question'], '→', row['actual_answer'])
    print(json.dumps({'cases': report['cases'], 'passed': report['passed']}, ensure_ascii=False))
    return int(report['cases'] != report['passed'])


if __name__ == '__main__':
    raise SystemExit(run())
