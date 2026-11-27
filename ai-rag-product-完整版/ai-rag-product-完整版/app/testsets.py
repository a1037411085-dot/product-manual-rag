"""APIs for editable test sets and repeatable evaluation runs."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, ValidationError

from . import core, experiments, llm_config, postgres, semantic, snapshots
from .testset_worker import FAILURE_LABELS, compare_rows


router = APIRouter(prefix='/api')
LOGGER = logging.getLogger(__name__)
MAX_CASES = 300
MAX_UPLOAD = 2 * 1024 * 1024
RUN_TIMEOUT = 1200
STATUSES = ('answered', 'no_answer', 'handoff', 'no_product', 'product_found')


class SetInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field('', max_length=2000)


class CaseInput(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    product_id: str = Field('', max_length=100)
    version: str = Field('', max_length=100)
    expected_status: Literal['answered', 'no_answer', 'handoff', 'no_product', 'product_found'] = 'answered'
    expected_answer: str = Field('', max_length=3000)
    expected_terms: list[str] = Field(default_factory=list)
    expected_source: str = Field('', max_length=500)
    expected_evidence: str = Field('', max_length=2000)
    history: list[str] = Field(default_factory=list)


class Variant(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    retriever: Literal['hash', 'bm25', 'semantic', 'chroma', 'hybrid'] = 'hash'
    answer_mode: Literal['auto', 'extractive', 'llm'] = 'extractive'
    model: str = Field('', max_length=200)
    threshold: Optional[float] = Field(None, ge=0, le=1)


class RunInput(BaseModel):
    variants: list[Variant] = Field(min_length=1, max_length=4)
    baseline_run_id: Optional[str] = None
    baseline_variant: int = Field(0, ge=0, le=3)
    allow_remote: bool = False


class ReviewInput(BaseModel):
    score: int = Field(ge=0, le=100, strict=True)
    note: str = Field('', max_length=2000)


def dump(value):
    return json.dumps(value, ensure_ascii=False)


@contextmanager
def transaction(write=False):
    with closing(core.connect()) as db, db:
        if write:
            if postgres.enabled():
                db.lock('testsets')
            else:
                db.execute('BEGIN IMMEDIATE')
        yield db


def require(row, label):
    if row is None:
        raise HTTPException(404, f'{label}不存在')
    return row


def tidy_items(values, label, count, max_length):
    result = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    if len(values) > count or any(len(value) > max_length for value in result):
        raise ValueError(f'{label}最多 {count} 项，每项最多 {max_length} 字')
    return result


def normalize_case(data):
    result = {key: value.strip() if isinstance(value, str) else value for key, value in data.model_dump().items()}
    if not result['question']:
        raise ValueError('问题不能为空')
    result['expected_terms'] = tidy_items(result['expected_terms'], '关键词', 30, 300)
    history = [value.strip() for value in result['history']]
    if len(history) > 5 or any(not value or len(value) > 1000 for value in history):
        raise ValueError('前置问句最多 5 条，每条 1–1000 字')
    result['history'] = history
    if bool(result['product_id']) != bool(result['version']):
        raise ValueError('产品 ID 和版本必须同时填写')
    if not result['product_id'] and result['expected_status'] not in ('no_product', 'product_found'):
        raise ValueError('该预期状态需要填写产品 ID 和版本')
    if result['expected_status'] == 'answered' and not (result['expected_answer'] or result['expected_terms']):
        raise ValueError('预期为有答案时，请填写参考答案或关键词')
    return result


@router.get('/test-sets')
def list_sets():
    with transaction() as db:
        rows = db.execute('''SELECT s.*,count(c.id) case_count FROM test_sets s LEFT JOIN test_cases c
                             ON s.id=c.set_id GROUP BY s.id ORDER BY s.updated_at DESC,s.rowid DESC''')
        return [dict(row) for row in rows]


@router.post('/test-sets', status_code=201)
def create_set(data: SetInput):
    set_id = uuid.uuid4().hex
    name = data.name.strip()
    if not name:
        raise HTTPException(400, '测试集名称不能为空')
    stamp = core.now()
    with transaction(write=True) as db:
        db.execute('INSERT INTO test_sets VALUES(?,?,?,?,?)', (set_id, name, data.description.strip(), stamp, stamp))
    return {'id': set_id, 'name': name}


@router.put('/test-sets/{set_id}')
def update_set(set_id: str, data: SetInput):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, '测试集名称不能为空')
    with transaction(write=True) as db:
        require(db.execute('UPDATE test_sets SET name=?,description=?,updated_at=? WHERE id=? RETURNING id',
                           (name, data.description.strip(), core.now(), set_id)).fetchone(), '测试集')
    return {'id': set_id, 'name': name}


@router.delete('/test-sets/{set_id}')
def delete_set(set_id: str):
    with transaction(write=True) as db:
        expire_runs(db)
        if db.execute("SELECT 1 FROM test_runs WHERE set_id=? AND status IN ('queued','running')", (set_id,)).fetchone():
            raise HTTPException(409, '请等待本测试集运行完成')
        require(db.execute('DELETE FROM test_sets WHERE id=? RETURNING id', (set_id,)).fetchone(), '测试集')
    return {'ok': True}


def stored_cases(db, set_id):
    return [{'id': row['id'], **json.loads(row['spec'])}
            for row in db.execute('SELECT id,spec FROM test_cases WHERE set_id=? ORDER BY rowid', (set_id,))]


@router.get('/test-sets/{set_id}')
def get_set(set_id: str):
    with transaction() as db:
        db.execute('BEGIN')
        group = require(db.execute('SELECT * FROM test_sets WHERE id=?', (set_id,)).fetchone(), '测试集')
        return {**dict(group), 'cases': stored_cases(db, set_id)}


def append_cases(db, set_id, cases):
    require(db.execute('SELECT id FROM test_sets WHERE id=?', (set_id,)).fetchone(), '测试集')
    existing = db.execute('SELECT count(*) FROM test_cases WHERE set_id=?', (set_id,)).fetchone()[0]
    if existing + len(cases) > MAX_CASES:
        raise HTTPException(400, f'每个测试集最多 {MAX_CASES} 条用例')
    ids = [uuid.uuid4().hex for _ in cases]
    db.executemany('INSERT INTO test_cases VALUES(?,?,?)',
                   [(case_id, set_id, dump(case)) for case_id, case in zip(ids, cases)])
    db.execute('UPDATE test_sets SET updated_at=? WHERE id=?', (core.now(), set_id))
    return ids


def validated_case(data):
    try:
        return normalize_case(data)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@router.post('/test-sets/{set_id}/cases', status_code=201)
def create_case(set_id: str, data: CaseInput):
    case = validated_case(data)
    with transaction(write=True) as db:
        return {'id': append_cases(db, set_id, [case])[0], **case}


@router.put('/test-sets/{set_id}/cases/{case_id}')
def update_case(set_id: str, case_id: str, data: CaseInput):
    case = validated_case(data)
    with transaction(write=True) as db:
        require(db.execute('UPDATE test_cases SET spec=? WHERE id=? AND set_id=? RETURNING id',
                           (dump(case), case_id, set_id)).fetchone(), '用例')
        db.execute('UPDATE test_sets SET updated_at=? WHERE id=?', (core.now(), set_id))
    return {'id': case_id, **case}


@router.delete('/test-sets/{set_id}/cases/{case_id}')
def delete_case(set_id: str, case_id: str):
    with transaction(write=True) as db:
        require(db.execute('DELETE FROM test_cases WHERE id=? AND set_id=? RETURNING id',
                           (case_id, set_id)).fetchone(), '用例')
        db.execute('UPDATE test_sets SET updated_at=? WHERE id=?', (core.now(), set_id))
    return {'ok': True}


COLUMNS = {
    'question': '问题', 'product_id': '产品ID', 'version': '版本', 'expected_status': '预期状态',
    'expected_answer': '参考答案', 'expected_terms': '关键词', 'expected_source': '期望来源',
    'expected_evidence': '期望证据', 'history': '前置问句',
}
HEADER_ALIASES = {alias.casefold(): key for key, chinese in COLUMNS.items() for alias in (key, chinese, chinese.replace('ID', ' ID'))}
STATUS_ALIASES = dict(zip(('有答案', '拒答', '转人工', '未找到产品', '找到产品'), STATUSES))


@contextmanager
def read_sheet(filename, content):
    extension = Path(filename).suffix.lower()
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError('文件须非空且不超过 2 MB')
    if extension == '.csv':
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = content.decode('gb18030')
        yield csv.reader(io.StringIO(text, newline=''), strict=True)
        return
    if extension != '.xlsx':
        raise ValueError('请选择 Excel (.xlsx) 或 CSV (.csv) 文件')
    from openpyxl import load_workbook
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(info.file_size for info in archive.infolist()) > 24 * 1024 * 1024:
            raise ValueError('Excel 解压后超过 24 MB')
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        if not workbook.worksheets:
            raise ValueError('Excel 中没有工作表')
        worksheet = workbook.worksheets[0]
        worksheet.reset_dimensions()
        yield worksheet.iter_rows(max_col=40, values_only=True)
    finally:
        workbook.close()


def parse_sheet(rows):
    rows = iter(rows)
    first = next(rows, None)
    if first is None:
        raise ValueError('表格为空')
    headers = [HEADER_ALIASES.get(str(value or '').strip().casefold()) for value in first]
    known = [name for name in headers if name]
    if 'question' not in known or len(known) != len(set(known)):
        raise ValueError('首行须包含“问题”或 question，且列名不能重复')
    parsed = []
    for number, values in enumerate(rows, 2):
        mapped = {key: str(value).strip() for key, value in zip(headers, values) if key and value is not None}
        if not any(mapped.values()):
            continue
        status = mapped.get('expected_status') or 'answered'
        mapped['expected_status'] = STATUS_ALIASES.get(status, status.casefold())
        for key in ('expected_terms', 'history'):
            mapped[key] = re.split(r'[|｜\n]+', mapped[key]) if mapped.get(key) else []
        try:
            parsed.append(normalize_case(CaseInput(**mapped)))
        except (ValidationError, ValueError) as error:
            detail = (f'{error.errors()[0]["loc"][0]} 字段无效' if isinstance(error, ValidationError) else str(error))
            raise ValueError(f'第 {number} 行：{detail}') from error
        if len(parsed) > MAX_CASES:
            raise ValueError(f'一次最多导入 {MAX_CASES} 条')
    if not parsed:
        raise ValueError('未读取到用例')
    return parsed


@router.get('/test-set-template.csv')
def template():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(COLUMNS.values())
    writer.writerow(['洗衣机额定洗涤容量是多少？', 'washer_wm090mja08hk', '1.0', '有答案', '9 kg', '9 kg', '', '', ''])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': 'attachment; filename="rag-test-template.csv"'})


@router.post('/test-sets/{set_id}/import', status_code=201)
def import_cases(set_id: str, file: UploadFile = File(...)):
    try:
        with read_sheet(file.filename or '', file.file.read(MAX_UPLOAD + 1)) as rows:
            cases = parse_sheet(rows)
    except ImportError as error:
        raise HTTPException(503, 'Excel 导入需要 openpyxl，请更新项目依赖') from error
    except (ValueError, csv.Error, zipfile.BadZipFile, KeyError, OSError, SyntaxError) as error:
        raise HTTPException(400, str(error) if isinstance(error, ValueError) else '无法读取表格，请检查文件格式') from error
    with transaction(write=True) as db:
        ids = append_cases(db, set_id, cases)
    return {'imported': len(ids)}


def knowledge_digest(db):
    digest = hashlib.sha256()
    queries = (
        'SELECT id,name,aliases FROM products ORDER BY id',
        'SELECT id,product_id,version,category,title,source FROM documents ORDER BY id',
        'SELECT document_id,text FROM chunks ORDER BY rowid',
        'SELECT document_id,question,answer,kind,category FROM faqs ORDER BY rowid',
        "SELECT value FROM settings WHERE key='answer_prompt'",
    )
    for query in queries:
        digest.update(query.encode())
        for row in db.execute(query):
            digest.update(dump(list(row)).encode())
    return digest.hexdigest()


def run_worker(environment, cases, variants, credentials):
    for key in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'):
        environment.pop(key, None)
    process = subprocess.run([sys.executable, '-m', 'app.testset_worker'], cwd=core.ROOT,
                             env=environment, input=dump({'cases': cases, 'variants': variants, 'llm': credentials}),
                             encoding='utf-8', capture_output=True, timeout=RUN_TIMEOUT, check=True)
    return json.loads(process.stdout)


def legacy_worker(cases, variants, credentials):
    with tempfile.TemporaryDirectory(prefix='rag-test-run-') as directory:
        snapshot = Path(directory) / 'knowledge.sqlite3'
        with closing(core.connect()) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
            with target:
                for table in ('test_sets', 'test_cases', 'test_runs', 'test_reviews'):
                    target.execute(f'DROP TABLE {table}')
                for table in ('sessions', 'messages', 'feedback', 'handoffs', 'unanswered'):
                    target.execute(f'DELETE FROM {table}')
                target.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('answer_prompt',?)",
                               (core.default_answer_prompt(),))
            target.execute('VACUUM')
            corpus_digest = knowledge_digest(target)
        environment = os.environ.copy()
        environment['RAG_DB'] = str(snapshot)
        return run_worker(environment, cases, variants, credentials), corpus_digest


def evaluate_run(run_id, cases, variants, credentials, baseline):
    try:
        with transaction(write=True) as db:
            db.execute("UPDATE test_runs SET status='running',updated_at=? WHERE id=?", (core.now(), run_id))
        if postgres.enabled():
            with snapshots.schema_snapshot() as schema:
                with closing(core.connect(schema)) as target:
                    corpus_digest = knowledge_digest(target)
                reports = run_worker(snapshots.environment(schema), cases, variants, credentials)
        else:
            reports, corpus_digest = legacy_worker(cases, variants, credentials)
        comparisons = [{'baseline': reports[0]['variant']['name'], 'candidate': item['variant']['name'],
                        **compare_rows(reports[0]['rows'], item['rows'])} for item in reports[1:]]
        if baseline:
            comparisons.extend({'baseline': baseline['label'], 'candidate': item['variant']['name'],
                                'baseline_run_id': baseline['id'], 'same_corpus': baseline['corpus_digest'] == corpus_digest,
                                **compare_rows(baseline['rows'], item['rows'])} for item in reports)
        report = {'variants': reports, 'comparisons': comparisons, 'failure_labels': FAILURE_LABELS}
        with transaction(write=True) as db:
            db.execute("UPDATE test_runs SET status='completed',updated_at=?,corpus_digest=?,report=? WHERE id=? AND status='running'",
                       (core.now(), corpus_digest, dump(report), run_id))
    except Exception as error:
        LOGGER.error('Test run %s failed (%s)', run_id, type(error).__name__)
        message = '批量执行超时，请缩小用例或方案范围' if isinstance(error, subprocess.TimeoutExpired) else '批量执行失败，请检查运行环境'
        with transaction(write=True) as db:
            db.execute("UPDATE test_runs SET status='failed',updated_at=?,error=? WHERE id=? AND status IN ('queued','running')",
                       (core.now(), message, run_id))


def expire_runs(db):
    db.execute("""UPDATE test_runs SET status='failed',updated_at=?,error='运行已中断或超时，请重新执行'
                  WHERE status IN ('queued','running')
                  AND updated_at < ?""",
               (core.now(), time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - RUN_TIMEOUT - 120))))
    if postgres.enabled() or os.name != 'posix':
        return
    active = db.execute("SELECT id,owner_pid FROM test_runs WHERE status IN ('queued','running')").fetchone()
    if active and active['owner_pid'] != os.getpid():
        try:
            os.kill(active['owner_pid'], 0)
        except ProcessLookupError:
            db.execute("UPDATE test_runs SET status='failed',updated_at=?,error='运行已中断，请重新执行' WHERE id=?",
                       (core.now(), active['id']))
        except PermissionError:
            pass


def run_metadata(row):
    fields = ('id', 'set_id', 'set_name', 'status', 'created_at', 'updated_at', 'dataset_digest', 'corpus_digest', 'error')
    return {**{field: row[field] for field in fields}, 'config': json.loads(row['config'])}


@router.get('/test-runs')
def list_runs(set_id: Optional[str] = None):
    with transaction(write=True) as db:
        expire_runs(db)
        rows = db.execute('''SELECT id,set_id,set_name,status,created_at,updated_at,dataset_digest,corpus_digest,error,config
                             FROM test_runs WHERE (? IS NULL OR set_id=?) ORDER BY rowid DESC LIMIT 100''',
                          (set_id, set_id))
        return [run_metadata(row) for row in rows]


def prepare_variants(variants):
    output = []
    provider = None
    for index, data in enumerate(variants):
        variant = data.model_dump()
        variant.update(name=data.name.strip(), model=data.model.strip(), id=str(index))
        if not variant['name'] or any(item['name'] == variant['name'] for item in output):
            raise HTTPException(400, '方案名称须非空且互不相同')
        if data.threshold is None:
            variant['threshold'] = 0.35 if data.retriever in ('semantic', 'chroma') else 0.16
        if data.answer_mode == 'llm':
            provider = provider or llm_config.effective(core.llm_settings_path())
            if os.getenv('RAG_LLM_DISABLED') == '1' or not all(provider[key] for key in ('base_url', 'api_key', 'model')):
                raise HTTPException(400, '请先在知识配置中保存可用的大模型服务与 API Key')
            variant['model'] = variant['model'] or provider['model']
            if any(ord(char) < 32 for char in variant['model']):
                raise HTTPException(400, '模型 ID 不能包含控制字符')
            variant['provider'] = provider['provider_id']
        else:
            variant['model'] = ''
        if data.retriever in ('semantic', 'chroma', 'hybrid'):
            embedding_provider, model, _, identity = semantic.config()
            variant.update(embedding_provider=embedding_provider, embedding_model=model, embedding_identity=identity)
        output.append(variant)
    return output, ({key: provider[key] for key in ('base_url', 'api_key')} if provider else {})


@router.post('/test-sets/{set_id}/runs', status_code=202)
def start_run(set_id: str, data: RunInput, tasks: BackgroundTasks):
    try:
        variants, credentials = prepare_variants(data.variants)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not data.allow_remote and any(v['answer_mode'] == 'llm' or v.get('embedding_provider') == 'api' for v in variants):
        raise HTTPException(400, '该方案会调用已配置的外部服务，请在批量评测中确认后执行')
    run_id = uuid.uuid4().hex
    baseline = None
    with transaction(write=True) as db:
        expire_runs(db)
        if db.execute("SELECT 1 FROM test_runs WHERE status IN ('queued','running')").fetchone():
            raise HTTPException(409, '已有批量评测正在运行')
        group = require(db.execute('SELECT name FROM test_sets WHERE id=?', (set_id,)).fetchone(), '测试集')
        cases = stored_cases(db, set_id)
        if not cases:
            raise HTTPException(400, '请先添加用例')
        if data.baseline_run_id:
            old = require(db.execute("SELECT * FROM test_runs WHERE id=? AND set_id=? AND status='completed'",
                                     (data.baseline_run_id, set_id)).fetchone(), '已完成的基线')
            options = json.loads(old['report'])['variants']
            if data.baseline_variant >= len(options):
                raise HTTPException(400, '请选择基线方案')
            selected = options[data.baseline_variant]
            baseline = {'id': old['id'], 'label': selected['variant']['name'] + ' · ' + old['created_at'],
                        'corpus_digest': old['corpus_digest'], 'rows': selected['rows']}
        config = {'variants': variants, 'case_count': len(cases), 'baseline_run_id': data.baseline_run_id,
                  'baseline_variant': data.baseline_variant}
        stamp = core.now()
        db.execute('''INSERT INTO test_runs(id,set_id,set_name,status,created_at,updated_at,owner_pid,dataset_digest,config)
                      VALUES(?,?,?,'queued',?,?,?,?,?)''',
                   (run_id, set_id, group['name'], stamp, stamp, os.getpid(), experiments.dataset_digest(cases), dump(config)))
    tasks.add_task(evaluate_run, run_id, cases, variants, credentials, baseline)
    return {'id': run_id, 'status': 'queued'}


def load_run(run_id, detailed):
    with transaction() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ' if postgres.enabled() else 'BEGIN')
        row = require(db.execute('SELECT * FROM test_runs WHERE id=?', (run_id,)).fetchone(), '评测记录')
        result = run_metadata(row)
        if detailed and row['report']:
            result.update(json.loads(row['report']))
            reviews = {(row['variant'], row['case_id']): {'score': row['score'], 'note': row['note'], 'updated_at': row['updated_at']}
                       for row in db.execute('SELECT * FROM test_reviews WHERE run_id=?', (run_id,))}
            for item in result['variants']:
                variant = item['variant']['id']
                for case in item['rows']:
                    case['review'] = reviews.get((variant, case['case']['id']))
                scores = [case['review']['score'] for case in item['rows'] if case['review'] is not None]
                item['metrics'].update(reviewed_cases=len(scores), human_average_score=round(sum(scores) / len(scores), 2) if scores else None)
    return result


@router.get('/test-runs/{run_id}')
def get_run(run_id: str):
    run = load_run(run_id, True)
    if run['status'] not in ('queued', 'running'):
        return run
    with transaction(write=True) as db:
        expire_runs(db)
    return load_run(run_id, True)


@router.put('/test-runs/{run_id}/variants/{variant_id}/cases/{case_id}/review')
def save_review(run_id: str, variant_id: str, case_id: str, data: ReviewInput):
    stamp = core.now()
    with transaction(write=True) as db:
        row = require(db.execute("SELECT report FROM test_runs WHERE id=? AND status='completed'", (run_id,)).fetchone(), '已完成的评测')
        variants = json.loads(row['report'])['variants']
        if not any(item['variant']['id'] == variant_id and any(r['case']['id'] == case_id for r in item['rows']) for item in variants):
            raise HTTPException(404, '评测用例不存在')
        db.execute('''INSERT INTO test_reviews VALUES(?,?,?,?,?,?)
                      ON CONFLICT(run_id,variant,case_id) DO UPDATE SET score=excluded.score,
                      note=excluded.note,updated_at=excluded.updated_at''',
                   (run_id, variant_id, case_id, data.score, data.note.strip(), stamp))
    return {'score': data.score, 'note': data.note.strip(), 'updated_at': stamp}


def markdown_report(run):
    def text(value):
        return str(value).replace('\\', '\\\\').replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')

    def percentage(value):
        return '—' if value is None else f'{value:.1%}'

    lines = [f'# RAG 评测报告 · {text(run["set_name"])}', '', f'运行 ID：{run["id"]}', '',
             f'时间：{run["created_at"]}', '', f'用例摘要：`{run["dataset_digest"]}`', '',
             f'资料摘要：`{run["corpus_digest"]}`', '',
             '自动评分统一格式及常见等价单位，同时给出严格字符串口径。人工评分独立汇总。', '',
             '| 方案 | 用例通过 | 严格通过 | 状态准确率 | 自动均分 | 人工均分（已评） | Hit@1 | Hit@3 | Hit@5 | MRR@5 | 拒答准确率 |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    for item in run['variants']:
        metrics = item['metrics']
        hits = [percentage(metrics['hit_at_k'][str(k)]) for k in (1, 3, 5)]
        strict = f'{metrics["strict_passed"]}/{metrics["total"]}' if 'strict_passed' in metrics else '—'
        lines.append('| ' + ' | '.join(map(str, [text(item['variant']['name']), f'{metrics["passed"]}/{metrics["total"]}', strict,
                    percentage(metrics['status_accuracy']), metrics['average_score'], f'{metrics["human_average_score"] if metrics["human_average_score"] is not None else "—"} ({metrics["reviewed_cases"]})',
                    *hits, metrics['mrr_at_5'] if metrics['mrr_at_5'] is not None else '—', percentage(metrics['refusal_accuracy'])])) + ' |')
    for item in run['variants']:
        variant = item['variant']
        models = item['metrics'].get('observed_models', {})
        diagnostics = item['metrics']['reason_counts']
        lines += ['', f'## {text(variant["name"])}', '',
                  f'- 检索：{variant["retriever"]}，阈值 {variant["threshold"]}',
                  f'- 回答模式：{variant["answer_mode"]}' + (f'，模型 {text(variant["model"])}' if variant['model'] else ''),
                  f'- 标注证据：{item["metrics"]["evidence_cases"]} 条']
        if models:
            lines.append('- 实际模型：' + '、'.join(f'{text(name)} × {count}' for name, count in models.items()))
        if diagnostics:
            lines.append('- 诊断分布：' + '、'.join(f'{FAILURE_LABELS[reason]} × {count}' for reason, count in diagnostics.items()))
        lines += ['', '### 逐题结果', '', '| 问题 | 预期 / 实际状态 | 自动分 | 严格分 | 人工分 | 证据排名 | 诊断 |',
                  '|---|---|---|---|---|---|---|']
        for row in item['rows']:
            lines.append('| ' + ' | '.join(map(str, [text(row['case']['question']),
                         f'{row["case"]["expected_status"]} / {row["actual_status"]}', row['score'], row.get('strict_score', '—'),
                         row['review']['score'] if row['review'] else '—', row['evidence_rank'] or '—',
                         '；'.join(FAILURE_LABELS[reason] for reason in row['reasons']) or '—'])) + ' |')
        failures = [row for row in item['rows'] if not row['passed']]
        if failures:
            lines.extend(['', '### 未通过样例（最多 10 条）', ''])
            for row in failures[:10]:
                case = row['case']
                expected = case['expected_answer'] or '、'.join(case['expected_terms']) or case['expected_status']
                lines.extend([f'- {text(case["question"])}', f'  - 预期：{text(expected)}',
                              f'  - 实际：{text(row["actual_answer"][:500]) or "—"}',
                              '  - 诊断：' + '、'.join(FAILURE_LABELS[reason] for reason in row['reasons'])])
    for item in run['comparisons']:
        lines += ['', f'## 回归对比 · {text(item["baseline"])} → {text(item["candidate"])}', '',
                  f'可比 {item["comparable"]} 题，退步 {len(item["regressions"])} 题，改善 {len(item["improvements"])} 题，'
                  f'新增 {len(item["new"])} 题，移除 {len(item["removed"])} 题，编辑 {len(item["edited"])} 题。']
        if item.get('same_corpus') is False:
            lines.extend(['', '本次与基线的知识资料快照有变化。'])
        if item.get('comparison_scoring') == 'literal':
            lines.extend(['', '历史评分版本不同，本次回归按双方的严格口径比较。'])
        for title, key in [('通过退步', 'regressions'), ('分数下降', 'score_drops'), ('证据排名下降', 'retrieval_drops')]:
            if item[key]:
                lines.extend(['', f'### {title}', ''])
                lines.extend(f'- {text(row["question"])}（分数 {row["before_score"]} → {row["after_score"]}，排名 {row["before_rank"] or "—"} → {row["after_rank"] or "—"}）' for row in item[key])
    return '\n'.join(lines) + '\n'


@router.get('/test-runs/{run_id}/report')
def download_report(run_id: str, format: Literal['md', 'json'] = 'md'):
    run = load_run(run_id, True)
    if run['status'] != 'completed':
        raise HTTPException(409, '评测完成后才能下载报告')
    return Response(markdown_report(run) if format == 'md' else dump(run),
                    media_type='text/markdown' if format == 'md' else 'application/json',
                    headers={'Content-Disposition': f'attachment; filename="rag-eval-{run_id}.{format}"'})
