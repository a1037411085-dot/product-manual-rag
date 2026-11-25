from __future__ import annotations

from contextlib import closing
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from threading import Lock
from typing import Optional
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from . import core, chroma_index, postgres, snapshots
from . import bm25
from . import semantic
from . import llm_config
from .testsets import router as testset_router
from migrate_sqlite import MigrationError, migrate

app = FastAPI(title='产品知识库问答演示', version='1.0')
core.init_db()
STATIC = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=STATIC), name='static')
app.include_router(testset_router)
logger = logging.getLogger(__name__)
migration_lock = Lock()
migration_job = {'status': 'idle'}

class ProductInput(BaseModel):
    id: str
    name: str
    aliases: list[str] = []

class AskInput(BaseModel):
    question: str
    session_id: Optional[str] = None
    product_id: Optional[str] = None
    version: Optional[str] = None

class FeedbackInput(BaseModel):
    session_id: str
    message_id: int = Field(gt=0)
    rating: int
    note: str = ''

class GapAnswerInput(BaseModel):
    product_id: str
    version: str
    question: str
    answer: str

class PromptInput(BaseModel):
    prompt: str

class LLMConfigInput(BaseModel):
    provider_id: str
    model: str = Field(default='', max_length=200)
    base_url: str = Field(default='', max_length=1000)
    api_key: Optional[str] = Field(default=None, max_length=4096)
    answer_mode: str = 'llm'

@app.get('/api/llm/config')
def get_llm_config():
    return {'providers': llm_config.PROVIDERS, **llm_config.public_config(core.llm_settings_path())}

@app.put('/api/llm/config')
def save_llm_config(data: LLMConfigInput):
    try:
        return llm_config.save(core.llm_settings_path(), provider_id=data.provider_id, model=data.model,
                               base_url=data.base_url, api_key=data.api_key, answer_mode=data.answer_mode)
    except ValueError as error:
        raise HTTPException(400, str(error))

@app.delete('/api/llm/config')
def reset_llm_config():
    return llm_config.reset(core.llm_settings_path())

@app.post('/api/llm/test')
def test_llm_config():
    import urllib.error
    import urllib.request
    if not llm_config.enabled(core.llm_settings_path()):
        raise HTTPException(400, '请先选择服务商并保存 API Key')
    config = llm_config.effective(core.llm_settings_path())
    payload = json.dumps({'model': config['model'], 'temperature': 0, 'max_tokens': 64,
                          'messages': [{'role': 'user', 'content': '请仅回复 OK。'}]}).encode()
    request = urllib.request.Request(config['base_url'] + '/chat/completions', data=payload,
            headers={'Authorization': 'Bearer ' + config['api_key'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
        content = result['choices'][0]['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError('empty response')
        return {'ok': True, 'model': str(result.get('model') or config['model']),
                'message': '连接成功，模型已返回内容。'}
    except Exception as error:
        kind = 'HTTP' + str(error.code) if isinstance(error, urllib.error.HTTPError) else type(error).__name__
        raise HTTPException(503, llm_config.error_message(kind))

@app.get('/')
def home():
    return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})


@app.get('/test-sets', include_in_schema=False)
def testset_page():
    return FileResponse(STATIC / 'testsets.html', headers={'Cache-Control': 'no-store'})

@app.get('/api/rag/status')
def rag_status():
    import importlib.util
    with closing(core.connect()) as db, db:
        chunks = db.execute('SELECT count(*) FROM chunks').fetchone()[0]
        indexed = db.execute('''SELECT count(*) FROM semantic_vectors s JOIN chunks c ON c.id=s.chunk_id
                              WHERE identity=?''', (semantic.config()[3],)).fetchone()[0]
        bm25_status = ({'available': True, 'built': True, 'pending_scopes': 0, 'implementation': 'postgres_scope'}
                       if postgres.enabled() else bm25.status(db))
    return {'retriever': os.getenv('RAG_RETRIEVER', core.default_retriever()),
            'answer_mode': llm_config.effective(core.llm_settings_path())['answer_mode'],
            'embedding_provider': semantic.config()[0], 'embedding_model': semantic.config()[1],
            'faiss_available': importlib.util.find_spec('faiss') is not None,
            'local_embedding_available': importlib.util.find_spec('sentence_transformers') is not None,
            'chunks': chunks, 'indexed_chunks': indexed, 'bm25': bm25_status,
            'database': 'postgresql' if postgres.enabled() else 'sqlite-test',
            'chroma_configured': chroma_index.configured(),
            'llm_configured': core.answer_prompt_config()['model_enabled']}

@app.post('/api/rag/reindex')
def reindex():
    try:
        if postgres.enabled() and os.getenv('RAG_RETRIEVER', core.default_retriever()) == 'chroma':
            return chroma_index.reindex(core.connect)
        return semantic.index_chunks(core.connect)
    except semantic.EmbeddingNotReady as error:
        raise HTTPException(503, str(error)) from error
    except Exception as error:
        raise HTTPException(503, '语义索引构建失败（' + type(error).__name__ + '），请检查依赖、模型和网络配置')


def migration_source():
    return core.ROOT / Path(os.getenv('RAG_LEGACY_DB') or 'data/rag.sqlite3').expanduser()


def migration_availability(source):
    if not postgres.enabled() or not chroma_index.configured():
        return '请先配置 PostgreSQL 和 Chroma 连接。'
    if not source.is_file():
        return '找不到旧库。请将文件放在 data/rag.sqlite3，或设置 RAG_LEGACY_DB 后重启。'
    return None


def update_migration_job(**values):
    with migration_lock:
        migration_job.update(values)


def migrate_legacy_database(source):
    try:
        result = migrate(source, progress=lambda message: update_migration_job(message=message))
    except MigrationError as error:
        update_migration_job(status='failed', message=str(error))
    except semantic.EmbeddingNotReady as error:
        update_migration_job(status='failed', message=str(error))
    except sqlite3.DatabaseError:
        update_migration_job(status='failed', message='无法读取旧 SQLite 库。请检查文件并停止旧服务写入后重试。')
    except Exception as error:
        logger.error('Legacy migration failed (%s)', type(error).__name__)
        update_migration_job(status='failed', message='迁移失败，请检查数据库、Chroma 和 Embedding 连接后重试。')
    else:
        update_migration_job(status='completed', message='迁移完成。', result=result)


@app.get('/api/rag/migration')
def migration_status():
    source = migration_source()
    problem = migration_availability(source)
    with migration_lock:
        return {'source': source.name, 'available': problem is None, 'problem': problem, **migration_job}


@app.post('/api/rag/migration', status_code=202)
def start_migration(request: Request, tasks: BackgroundTasks):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, '迁移请求必须使用 JSON。')
    source = migration_source()
    if problem := migration_availability(source):
        raise HTTPException(409, problem)
    with migration_lock:
        if migration_job['status'] != 'running':
            migration_job.clear()
            migration_job.update(status='running', message='正在检查 PostgreSQL 和 Chroma 连接…')
            tasks.add_task(migrate_legacy_database, source)
        return dict(migration_job)


@app.post('/api/rag/reindex/bm25')
def reindex_bm25():
    if postgres.enabled():
        with closing(core.connect()) as db:
            return {'available': True, 'built': True, 'pending_scopes': 0,
                    'chunks': db.execute('SELECT count(*) FROM chunks').fetchone()[0]}
    with closing(core.connect()) as db, db:
        if not bm25.available(db):
            raise HTTPException(503, '当前 SQLite 未启用 FTS5，BM25 查询会使用字符检索')
        bm25.refresh(db)
        return {**bm25.status(db), 'chunks': db.execute('SELECT count(*) FROM rag_bm25').fetchone()[0]}

@app.get('/api/products')
def products():
    with closing(core.connect()) as db, db:
        rows = db.execute('SELECT * FROM products ORDER BY name').fetchall()
        result = []
        for p in rows:
            versions = [r['version'] for r in db.execute(
                'SELECT DISTINCT version FROM documents WHERE product_id=? ORDER BY version DESC', (p['id'],))]
            result.append({'id': p['id'], 'name': p['name'], 'aliases': json.loads(p['aliases']), 'versions': versions})
        return result

@app.get('/api/products/featured')
def featured_products():
    catalog = core.product_catalog()
    return catalog[:6] if any(p['consultation_count'] for p in catalog) else catalog

@app.get('/api/products/catalog')
def indexed_products():
    return core.product_catalog()

@app.get('/api/products/search')
def search_products(q: str):
    if len(q) > 200:
        raise HTTPException(400, '搜索内容不能超过 200 字')
    return core.search_products(q)

@app.post('/api/products')
def create_product(data: ProductInput):
    try:
        core.add_product(data.id, data.name, data.aliases)
        return {'ok': True}
    except ValueError as e:
        raise HTTPException(400, str(e))

async def document_request(product_id, version, category, title, file,
                           remove_page_markers, remove_duplicates, max_chars, overlap,
                           selected_faqs=None, preview=False):
    try:
        content = await file.read(5 * 1024 * 1024 + 1)
        settings = dict(remove_page_markers=remove_page_markers,
                        remove_duplicates=remove_duplicates, max_chars=max_chars,
                        overlap=overlap, include_training=True)
        if preview:
            result = await run_in_threadpool(core.prepare_document, product_id, version, file.filename, content, **settings)
            result.pop('product_name')
            return result
        selection = json.loads(selected_faqs) if selected_faqs is not None else None
        return await run_in_threadpool(core.add_document, product_id, version.strip(), category.strip(),
                                       title.strip() or file.filename, file.filename, content,
                                       selected_faqs=selection, **settings)
    except (ValueError, UnicodeError, zipfile.BadZipFile, ImportError, KeyError) as e:
        raise HTTPException(400, str(e))


@app.post('/api/documents/preview')
async def preview_document(product_id: str = Form(...), version: str = Form(...),
                           file: UploadFile = File(...),
                           remove_page_markers: bool = Form(True),
                           remove_duplicates: bool = Form(True),
                           max_chars: int = Form(360), overlap: int = Form(50)):
    return await document_request(product_id, version, '', '', file,
                                  remove_page_markers, remove_duplicates, max_chars, overlap,
                                  preview=True)


@app.post('/api/documents')
async def upload_document(product_id: str = Form(...), version: str = Form(...),
                          category: str = Form('产品说明'), title: str = Form(''),
                          file: UploadFile = File(...),
                          remove_page_markers: bool = Form(True),
                          remove_duplicates: bool = Form(True),
                          max_chars: int = Form(360), overlap: int = Form(50),
                          selected_faqs: Optional[str] = Form(None)):
    return await document_request(product_id, version, category, title, file,
                                  remove_page_markers, remove_duplicates, max_chars, overlap,
                                  selected_faqs)

@app.get('/api/documents')
def documents(product_id: Optional[str] = None):
    with closing(core.connect()) as db, db:
        if product_id:
            rows = db.execute('SELECT d.*, count(c.id) chunks, (SELECT count(*) FROM faqs f WHERE f.document_id=d.id) faqs FROM documents d LEFT JOIN chunks c ON c.document_id=d.id WHERE d.product_id=? GROUP BY d.id ORDER BY d.created_at DESC', (product_id,))
        else:
            rows = db.execute('SELECT d.*, count(c.id) chunks, (SELECT count(*) FROM faqs f WHERE f.document_id=d.id) faqs FROM documents d LEFT JOIN chunks c ON c.document_id=d.id GROUP BY d.id ORDER BY d.created_at DESC')
        return [{**dict(r), 'cleaning_report': json.loads(r['cleaning_report'])} for r in rows]

@app.get('/api/faqs')
def faqs(product_id: str, version: str, limit: int = 12):
    return core.list_faqs(product_id, version, limit)


@app.get('/api/documents/{document_id}/faqs')
def document_faqs(document_id: str, q: str = Query('', max_length=500),
                  offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    with closing(core.connect()) as db, db:
        document = db.execute('SELECT id,product_id,version,title,source FROM documents WHERE id=?',
                              (document_id,)).fetchone()
        if document is None:
            raise HTTPException(404, '文档不存在')
        where = 'FROM faqs f LEFT JOIN faq_origins o ON o.faq_id=f.id WHERE f.document_id=?'
        term = q.strip()
        parameters = (document_id,)
        if term:
            substring = 'strpos' if postgres.enabled() else 'instr'
            where += f" AND {substring}(lower(f.question || ' ' || f.answer), lower(?))>0"
            parameters += (term,)
        count = db.execute('SELECT count(*) ' + where, parameters).fetchone()[0]
        rows = db.execute('''SELECT f.id,f.question,f.answer,f.category,f.kind,o.source_line,o.source_end_line ''' +
                          where + ' ORDER BY f.rowid LIMIT ? OFFSET ?', (*parameters, limit, offset))
        return {'document': dict(document), 'total': count, 'items': [dict(row) for row in rows]}

@app.post('/api/faqs/train')
def train_faqs(product_id: Optional[str] = None, version: Optional[str] = None):
    return core.bootstrap_faqs(product_id, version)

@app.delete('/api/documents/{document_id}')
def delete_document(document_id: str):
    with closing(core.connect()) as db, db:
        row = db.execute('DELETE FROM documents WHERE id=? RETURNING id', (document_id,)).fetchone()
        if not row:
            raise HTTPException(404, '文档不存在')
    return {'ok': True}

@app.post('/api/ask')
def ask(data: AskInput):
    try:
        return core.ask(data.question, data.session_id, data.product_id, data.version)
    except core.ConversationConflictError as error:
        raise HTTPException(409, str(error)) from error
    except semantic.EmbeddingNotReady as error:
        raise HTTPException(503, str(error)) from error
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as error:
        raise HTTPException(503, '问答服务暂不可用（' + type(error).__name__ + '），请检查检索配置')

@app.post('/api/feedback')
def feedback(data: FeedbackInput):
    if data.rating not in (-1, 1):
        raise HTTPException(400, '请选择有帮助或没帮助')
    with closing(core.connect()) as db, db:
        message = db.execute('SELECT id FROM messages WHERE id=? AND session_id=? AND role=? AND status=?',
                             (data.message_id, data.session_id, 'assistant', 'answered')).fetchone()
        if not message:
            raise HTTPException(404, '对应回答不存在或不可评价')
        db.execute('''INSERT INTO feedback(session_id,message_id,rating,note,created_at)
                      VALUES(?,?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET
                      rating=excluded.rating,note=excluded.note,created_at=excluded.created_at''',
                   (data.session_id, data.message_id, data.rating, data.note[:500], core.now()))
    return {'ok': True, 'message_id': data.message_id, 'rating': data.rating}

@app.post('/api/gaps/{gap_id}/answer')
def answer_gap(gap_id: int, data: GapAnswerInput):
    try:
        return core.supplement_gap(gap_id, data.product_id, data.version,
                                   data.question, data.answer)
    except KeyError as e:
        raise HTTPException(404, e.args[0])
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post('/api/faqs/manual')
def add_manual_faq(data: GapAnswerInput):
    try:
        return core.supplement_gap(None, data.product_id, data.version,
                                   data.question, data.answer)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.get('/api/prompt')
def get_prompt():
    return core.answer_prompt_config()

@app.put('/api/prompt')
def update_prompt(data: PromptInput):
    try:
        return core.save_answer_prompt(data.prompt)
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.delete('/api/prompt')
def reset_prompt():
    return core.reset_answer_prompt()

@app.get('/api/stats')
def stats():
    with closing(core.connect()) as db, db:
        counts = dict(db.execute("SELECT status,count(*) n FROM messages WHERE role='assistant' GROUP BY status").fetchall())
        total = sum(counts.values())
        return {'questions': total, 'answered': counts.get('answered', 0),
                'handoffs': counts.get('handoff', 0),
                'unanswered': counts.get('no_answer', 0) + counts.get('no_product', 0),
                'auto_faqs': db.execute("SELECT count(*) FROM faqs WHERE kind IN ('auto','rule','trained','parameter','procedure','troubleshooting')").fetchone()[0],
                'open_gap_count': db.execute('SELECT count(*) FROM unanswered WHERE resolved_at IS NULL').fetchone()[0],
                'resolution_rate': round(counts.get('answered', 0) / total, 3) if total else 0,
                'feedback_positive': db.execute('SELECT count(*) FROM feedback WHERE rating=1').fetchone()[0],
                'feedback_negative': db.execute('SELECT count(*) FROM feedback WHERE rating=-1').fetchone()[0],
                'top_unanswered': [dict(r) for r in db.execute('''
                    SELECT min(u.id) id,u.question,u.product_id,u.version,
                           coalesce(p.name,'未确认产品') product_name,count(*) count
                    FROM unanswered u LEFT JOIN products p ON p.id=u.product_id
                    WHERE u.resolved_at IS NULL
                    GROUP BY u.question,u.product_id,u.version,p.name
                    ORDER BY count DESC,id LIMIT 10''')]}

@app.get('/api/handoffs')
def handoffs():
    with closing(core.connect()) as db, db:
        return [dict(r) for r in db.execute('SELECT * FROM handoffs ORDER BY created_at DESC LIMIT 50')]


@app.get('/api/evaluation')
def evaluation():
    """Run fixed evaluation cases against an isolated copy of the knowledge."""
    with closing(core.connect()) as db:
        history = [dict(row) for row in db.execute('''
            SELECT id,created_at,product_id,version,documents_scanned,faqs_added,total_faqs
            FROM training_runs ORDER BY id''')]
    if postgres.enabled():
        with snapshots.schema_snapshot() as schema:
            result = execute_evaluation(snapshots.environment(schema))
    else:
        with tempfile.TemporaryDirectory(prefix='rag-evaluation-') as temp:
            snapshot = Path(temp) / 'snapshot.sqlite3'
            with closing(core.connect()) as db, closing(sqlite3.connect(snapshot)) as target:
                db.backup(target)
            result = execute_evaluation({**os.environ, 'RAG_DB': str(snapshot)})
    return {'training_history': history, **result}


def execute_evaluation(environment):
    environment['RAG_LLM_DISABLED'] = '1'
    environment['RAG_ANSWER_MODE'] = 'extractive'
    for key in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'):
        environment.pop(key, None)
    try:
        process = subprocess.run([sys.executable, '-m', 'app.evaluation_worker'], cwd=core.ROOT,
                                 env=environment, capture_output=True, text=True, timeout=45, check=True)
        return json.loads(process.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as error:
        raise HTTPException(500, '评估运行失败，请检查服务日志') from error
