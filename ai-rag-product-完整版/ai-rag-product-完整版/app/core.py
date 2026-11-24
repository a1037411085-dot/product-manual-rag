"""Product-scoped retrieval and conversation logic for the classroom demo."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.request
import urllib.error
import uuid
import zipfile
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer
from .cleaning import clean_document
from .faq import ANSWER, QUESTION, extract_faqs, extract_import_faqs, extract_training_faqs
from .html_extract import extract_html_text
from . import semantic
from . import llm_config
from . import query
from . import bm25

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.getenv('RAG_DB', ROOT / 'data' / 'rag.sqlite3'))
VECTORIZER = HashingVectorizer(analyzer='char', ngram_range=(2, 4), n_features=4096,
                               alternate_sign=False, norm='l2', lowercase=True)
NO_ANSWER = '现有产品资料中暂未找到相关说明'


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    # 大结果排序会落盘临时文件；TEMP 不可写时 SQLite 报 unable to open database file
    db.execute('PRAGMA temp_store=MEMORY')
    return db


def init_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, product_id TEXT NOT NULL, version TEXT NOT NULL,
            category TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
            cleaning_report TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(product_id) REFERENCES products(id));
        CREATE TABLE IF NOT EXISTS document_texts(document_id TEXT PRIMARY KEY, text TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT NOT NULL, product_id TEXT NOT NULL,
            version TEXT NOT NULL, category TEXT NOT NULL, source TEXT NOT NULL, text TEXT NOT NULL,
            vector BLOB NOT NULL, FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS faqs(id TEXT PRIMARY KEY, document_id TEXT NOT NULL, product_id TEXT NOT NULL,
            version TEXT NOT NULL, category TEXT NOT NULL, source TEXT NOT NULL, question TEXT NOT NULL,
            answer TEXT NOT NULL, kind TEXT NOT NULL, vector BLOB NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS faq_origins(faq_id TEXT PRIMARY KEY, source_line INTEGER NOT NULL,
            source_end_line INTEGER NOT NULL, FOREIGN KEY(faq_id) REFERENCES faqs(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, product_id TEXT, version TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            role TEXT NOT NULL, text TEXT NOT NULL, status TEXT, created_at TEXT NOT NULL, product_id TEXT);
        CREATE TABLE IF NOT EXISTS handoffs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            message_id INTEGER, rating INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS unanswered(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            question TEXT NOT NULL, product_id TEXT, version TEXT, created_at TEXT NOT NULL,
            resolved_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS training_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL, product_id TEXT, version TEXT,
            documents_scanned INTEGER NOT NULL, faqs_added INTEGER NOT NULL,
            total_faqs INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS test_sets(id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS test_cases(id TEXT PRIMARY KEY, set_id TEXT NOT NULL, spec TEXT NOT NULL,
            FOREIGN KEY(set_id) REFERENCES test_sets(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_test_cases_set ON test_cases(set_id);
        CREATE TABLE IF NOT EXISTS test_runs(id TEXT PRIMARY KEY, set_id TEXT NOT NULL, set_name TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, owner_pid INTEGER NOT NULL,
            dataset_digest TEXT NOT NULL, corpus_digest TEXT, config TEXT NOT NULL, report TEXT, error TEXT);
        CREATE INDEX IF NOT EXISTS idx_test_runs_set ON test_runs(set_id, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_test_run_active ON test_runs((1)) WHERE status IN ('queued','running');
        CREATE TABLE IF NOT EXISTS test_reviews(run_id TEXT NOT NULL, variant TEXT NOT NULL, case_id TEXT NOT NULL,
            score INTEGER NOT NULL, note TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(run_id,variant,case_id), FOREIGN KEY(run_id) REFERENCES test_runs(id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(product_id, version);
        CREATE INDEX IF NOT EXISTS idx_faqs_scope ON faqs(product_id, version);
        CREATE INDEX IF NOT EXISTS idx_faqs_document ON faqs(document_id);
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id,id);
        ''')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(documents)')}
        if 'cleaning_report' not in columns:
            db.execute("ALTER TABLE documents ADD COLUMN cleaning_report TEXT NOT NULL DEFAULT '{}'")
        message_columns = {row['name'] for row in db.execute('PRAGMA table_info(messages)')}
        if 'product_id' not in message_columns:
            db.execute('ALTER TABLE messages ADD COLUMN product_id TEXT')
            db.execute('''UPDATE messages SET product_id=(
                SELECT product_id FROM sessions WHERE sessions.id=messages.session_id)
                WHERE role='user' ''')
        if 'version' not in message_columns:
            db.execute('ALTER TABLE messages ADD COLUMN version TEXT')
        unanswered_columns = {row['name'] for row in db.execute('PRAGMA table_info(unanswered)')}
        if 'version' not in unanswered_columns:
            db.execute('ALTER TABLE unanswered ADD COLUMN version TEXT')
            db.execute('''UPDATE unanswered SET version=(
                SELECT version FROM sessions WHERE sessions.id=unanswered.session_id)''')
        if 'resolved_at' not in unanswered_columns:
            db.execute('ALTER TABLE unanswered ADD COLUMN resolved_at TEXT')
        feedback_columns = {row['name'] for row in db.execute('PRAGMA table_info(feedback)')}
        if 'message_id' not in feedback_columns:
            db.execute('ALTER TABLE feedback ADD COLUMN message_id INTEGER')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_unanswered_open ON unanswered(resolved_at,product_id,version)')
        semantic.ensure_schema(db)


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def default_answer_prompt():
    return (ROOT / 'prompts' / 'answer.txt').read_text(encoding='utf-8')


def answer_prompt_config():
    default = default_answer_prompt()
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='answer_prompt'").fetchone()
    return {'prompt': row['value'] if row else default,
            'default_prompt': default,
            'source': 'custom' if row else 'default',
            'model_enabled': llm_config.enabled(DB_PATH)}


def save_answer_prompt(prompt):
    prompt = prompt.strip()
    if not 10 <= len(prompt) <= 10000:
        raise ValueError('Prompt 长度需为 10–10000 字')
    with connect() as db:
        db.execute("""INSERT INTO settings(key,value) VALUES('answer_prompt',?)
                      ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (prompt,))
    return answer_prompt_config()


def reset_answer_prompt():
    with connect() as db:
        db.execute("DELETE FROM settings WHERE key='answer_prompt'")
    return answer_prompt_config()


def add_product(product_id, name, aliases=None):
    product_id, name = product_id.strip(), name.strip()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{2,40}', product_id):
        raise ValueError('产品 ID 需为 2–40 位字母、数字、下划线或短横线')
    if not name:
        raise ValueError('产品名称不能为空')
    with connect() as db:
        db.execute('INSERT INTO products VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, aliases=excluded.aliases',
                   (product_id, name, json.dumps(aliases or [], ensure_ascii=False)))


def parse_document(filename: str, content: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext in ('.txt', '.md'):
        return content.decode('utf-8-sig')
    if ext in ('.html', '.htm'):
        return extract_html_text(content)
    if ext == '.docx':
        from io import BytesIO
        with zipfile.ZipFile(BytesIO(content)) as z:
            root = ET.fromstring(z.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        return '\n'.join(''.join(t.text or '' for t in p.findall('.//w:t', ns))
                         for p in root.findall('.//w:p', ns))
    if ext == '.pdf':
        from io import BytesIO
        from pypdf import PdfReader
        return '\n'.join(page.extract_text() or '' for page in PdfReader(BytesIO(content)).pages)
    raise ValueError('仅支持 .txt、.md、.html、.htm、.docx、.pdf')


def split_text(text: str, max_chars=360, overlap=50):
    # The cleaner has already normalized line endings and whitespace. A heading
    # stays with each following fact so identical words in different sections
    # retain their context. Short label/value lines are joined before indexing.
    chunks, heading, pending = [], '', []

    def emit(body):
        prefix = heading + '\n' if heading else ''
        room = max(1, max_chars - len(prefix))
        if len(body) <= room:
            chunks.append(prefix + body)
        else:
            step = max(1, room - min(overlap, room // 2))
            for start in range(0, len(body), step):
                chunks.append(prefix + body[start:start + room])

    for line in text.splitlines():
        if line.startswith('#'):
            if pending:
                emit(' '.join(pending))
                pending = []
            heading = line.lstrip('#').strip()[:80]
            continue
        if QUESTION.match(line) or line.endswith(('?', '？')):
            continue
        answer_marker = ANSWER.match(line)
        if answer_marker:
            line = answer_marker.group(1).strip()
        if len(line) < 6:
            pending.append(line)
            continue
        if pending:
            line = ' '.join(pending + [line])
            pending = []
        emit(line)
    if pending:
        emit(' '.join(pending))
    return [c for c in chunks if c.strip()]


def prepare_document(product_id, version, filename, content, *, remove_page_markers=True,
                     remove_duplicates=True, max_chars=360, overlap=50, include_training=False):
    if not version.strip():
        raise ValueError('版本不能为空')
    if not 120 <= max_chars <= 2000 or not 0 <= overlap < max_chars // 2:
        raise ValueError('片段长度须为 120–2000，重叠须小于片段长度的一半')
    if len(content) > 5 * 1024 * 1024:
        raise ValueError('文件不能超过 5 MB')
    with connect() as db:
        product = db.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone()
        if not product:
            raise ValueError('产品不存在')
    raw_text = parse_document(filename, content)
    text, cleaning_report = clean_document(raw_text, remove_page_markers=remove_page_markers,
                                          remove_duplicates=remove_duplicates)
    extractor = extract_import_faqs if include_training else extract_faqs
    faqs = extractor(text, product['name'])
    chunks = split_text(text, max_chars, overlap)
    if include_training:
        headers = query.table_headers(text)
        if headers:
            for index, chunk in enumerate(chunks):
                body = chunk.split('\n', 1)[-1]
                if body in headers and len(headers[body]) + len(body) < max_chars:
                    chunks[index] = f'{headers[body]}\n{body}'
        chunks.extend(f'{heading}\n{fact}' for _, heading, fact in query.model_sections(text)
                      if len(heading) + len(fact) < max_chars)
    chunk_bodies = {chunk.split('\n', 1)[-1] for chunk in chunks}
    for faq in faqs:
        answer = faq['answer']
        if answer not in chunk_bodies and not any(answer in chunk for chunk in chunks):
            if include_training and '\n' in answer and len(answer) + len(faq['category']) < max_chars:
                chunks.append(f"{faq['category']}\n{answer}")
            else:
                chunks.extend(split_text(answer, max_chars, overlap))
    chunks = list(dict.fromkeys(chunks))
    if not chunks:
        raise ValueError('文档中没有可索引的文字')
    cleaning_report['settings'] = {'remove_page_markers': remove_page_markers,
                                  'remove_duplicates': remove_duplicates,
                                  'max_chars': max_chars, 'overlap': overlap}
    return {'text': text, 'cleaning': cleaning_report, 'chunks': chunks, 'faqs': faqs,
            'product_name': product['name']}


def add_document(product_id, version, category, title, filename, content, *,
                 selected_faqs=None, **settings):
    prepared = prepare_document(product_id, version, filename, content, **settings)
    text, cleaning_report = prepared['text'], prepared['cleaning']
    chunks, faqs = prepared['chunks'], prepared['faqs']
    product = {'name': prepared['product_name']}
    if selected_faqs is not None:
        # Only source-backed answers from this preview can be indexed. Operators
        # may refine the question or omit a candidate, but cannot invent an answer.
        selected, seen = [], set()
        if not isinstance(selected_faqs, list) or len(selected_faqs) > len(faqs):
            raise ValueError('QA 选择格式无效，请重新预览')
        for item in selected_faqs:
            if not isinstance(item, dict):
                raise ValueError('QA 选择格式无效')
            index = item.get('index')
            question = item.get('question', '')
            if (type(index) is not int or not 0 <= index < len(faqs) or index in seen
                    or not isinstance(question, str) or not 1 <= len(question.strip()) <= 500):
                raise ValueError('QA 问题不能为空、不能超过 500 字，且不得重复选择')
            seen.add(index)
            selected.append({**faqs[index], 'question': question.strip()})
        faqs = selected
    doc_id = uuid.uuid4().hex
    with connect() as db:
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                   (doc_id, product_id, version, category, title, filename, now(),
                    json.dumps(cleaning_report, ensure_ascii=False)))
        db.execute('INSERT INTO document_texts(document_id,text) VALUES(?,?)', (doc_id, text))
        for start in range(0, len(chunks), 128):
            batch = chunks[start:start + 128]
            # Index the factual body and keep the heading in stored evidence.
            vectors = VECTORIZER.transform([c.split('\n', 1)[-1] for c in batch]).astype(np.float32).toarray()
            db.executemany('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                           [(uuid.uuid4().hex, doc_id, product_id, version, category, filename,
                             c, vector.tobytes()) for c, vector in zip(batch, vectors)])
        for start in range(0, len(faqs), 128):
            batch = faqs[start:start + 128]
            faq_vectors = VECTORIZER.transform(
                [faq_terms(f['question'], product['name']) for f in batch]).astype(np.float32).toarray()
            faq_ids = [uuid.uuid4().hex for _ in batch]
            db.executemany('INSERT INTO faqs VALUES(?,?,?,?,?,?,?,?,?,?)',
                           [(faq_id, doc_id, product_id, version, f['category'], filename,
                             f['question'], f['answer'], f['kind'], vector.tobytes())
                            for faq_id, f, vector in zip(faq_ids, batch, faq_vectors)])
            db.executemany('INSERT INTO faq_origins VALUES(?,?,?)',
                           [(faq_id, f['source_line'], f['source_end_line'])
                            for faq_id, f in zip(faq_ids, batch) if 'source_line' in f])
    return {'document_id': doc_id, 'chunks': len(chunks), 'characters': len(text),
            'faqs': len(faqs), 'faq_preview': [f['question'] for f in faqs[:6]],
            'cleaning': cleaning_report}


def reconstruct_chunk_text(db, document_id):
    """Recover headings and overlapped long lines from older indexed documents."""
    lines, current_heading = [], None
    for row in db.execute('SELECT text FROM chunks WHERE document_id=? ORDER BY rowid',
                          (document_id,)):
        chunk = row['text']
        if '\n' in chunk:
            heading, body = chunk.split('\n', 1)
            if heading != current_heading:
                lines.append('# ' + heading)
                current_heading = heading
        else:
            body = chunk
            current_heading = None
        if not body.strip():
            continue
        # Long original lines were cut with a 50-character overlap.
        overlap = 0
        if lines and not lines[-1].startswith('# '):
            for size in range(min(80, len(body), len(lines[-1])), 19, -1):
                if lines[-1].endswith(body[:size]):
                    overlap = size
                    break
        if overlap:
            lines[-1] += body[overlap:]
        else:
            lines.append(body)
    return '\n'.join(lines)


def bootstrap_faqs(product_id=None, version=None):
    """On demand, fill missing source-backed FAQs without changing model weights."""
    added = scanned = already_present = source_rejected = empty_documents = 0
    with connect() as db:
        documents = db.execute('''SELECT d.id,d.product_id,d.version,d.category,d.source,
                                         p.name product_name
                                  FROM documents d JOIN products p ON p.id=d.product_id
                                  WHERE d.source != '人工补充'
                                    AND (? IS NULL OR d.product_id=?)
                                    AND (? IS NULL OR d.version=?)
                                  ORDER BY d.rowid''',
                               (product_id, product_id, version, version)).fetchall()
        for doc in documents:
            scanned += 1
            stored = db.execute('SELECT text FROM document_texts WHERE document_id=?',
                                (doc['id'],)).fetchone()
            source_text = (stored['text'] if stored else '') or reconstruct_chunk_text(db, doc['id'])
            if not source_text.strip():
                empty_documents += 1
                continue
            source_lines = set(source_text.splitlines())
            proposals = []
            for faq in extract_faqs(source_text, doc['product_name']) + extract_training_faqs(source_text, doc['product_name']):
                key = faq_terms(faq['question'], doc['product_name'])
                answer_lines = [line.strip() for line in faq['answer'].splitlines() if line.strip()]
                # Every answer line must be copied from this document's text.
                if not key or not answer_lines or any(
                        line not in source_lines and line not in source_text for line in answer_lines):
                    source_rejected += 1
                    continue
                proposals.append((key, faq))
            if not proposals:
                continue
            # Lock only while writing this document, then release it for chat requests.
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM documents WHERE id=?', (doc['id'],)).fetchone():
                db.rollback()
                continue
            existing = {faq_terms(row['question'], doc['product_name']) for row in db.execute(
                'SELECT question FROM faqs WHERE document_id=?', (doc['id'],))}
            candidates = []
            for key, faq in proposals:
                if key in existing:
                    already_present += 1
                    continue
                existing.add(key)
                candidates.append((key, faq))
            for start in range(0, len(candidates), 64):
                batch = candidates[start:start + 64]
                vectors = VECTORIZER.transform([key for key, _ in batch]).astype(np.float32).toarray()
                db.executemany('INSERT INTO faqs VALUES(?,?,?,?,?,?,?,?,?,?)', [
                    (uuid.uuid4().hex, doc['id'], doc['product_id'], doc['version'],
                     faq['category'], doc['source'], faq['question'], faq['answer'],
                     faq['kind'], vector.tobytes())
                    for (_, faq), vector in zip(batch, vectors)])
            db.commit()
            added += len(candidates)
        total_faqs = db.execute('''SELECT count(*) FROM faqs
                                  WHERE (? IS NULL OR product_id=?)
                                    AND (? IS NULL OR version=?)''',
                                (product_id, product_id, version, version)).fetchone()[0]
        db.execute('''INSERT INTO training_runs
                      (created_at,product_id,version,documents_scanned,faqs_added,total_faqs)
                      VALUES(?,?,?,?,?,?)''',
                   (now(), product_id, version, scanned, added, total_faqs))
    return {'documents_scanned': scanned, 'faqs_added': added,
            'already_present': already_present, 'source_rejected': source_rejected,
            'empty_documents': empty_documents, 'faqs_total': total_faqs}


def product_catalog(limit=None):
    """Return indexed products ordered by the number of user consultations."""
    sql = '''
        SELECT p.id, p.name, p.aliases,
               (SELECT count(*) FROM documents d WHERE d.product_id=p.id) AS document_count,
               (SELECT count(*) FROM messages m
                WHERE m.role='user' AND m.product_id=p.id) AS consultation_count
        FROM products p
        WHERE EXISTS (SELECT 1 FROM documents d WHERE d.product_id=p.id)
        ORDER BY consultation_count DESC, p.name COLLATE NOCASE, p.id
    '''
    with connect() as db:
        rows = db.execute(sql + (' LIMIT ?' if limit is not None else ''),
                          (limit,) if limit is not None else ()).fetchall()
        catalog = []
        for row in rows:
            versions = [r['version'] for r in db.execute(
                'SELECT DISTINCT version FROM documents WHERE product_id=? ORDER BY version DESC', (row['id'],))]
            categories = [r['category'] for r in db.execute(
                'SELECT DISTINCT category FROM documents WHERE product_id=? ORDER BY category', (row['id'],))]
            sample = db.execute('''SELECT c.text FROM chunks c JOIN documents d ON d.id=c.document_id
                                   WHERE c.product_id=? ORDER BY d.version DESC, c.rowid LIMIT 1''',
                                (row['id'],)).fetchone()
            catalog.append({'id': row['id'], 'name': row['name'],
                            'aliases': json.loads(row['aliases']), 'versions': versions,
                            'categories': categories, 'document_count': row['document_count'],
                            'consultation_count': row['consultation_count'],
                            'summary': sample['text'].split('\n', 1)[-1][:80] if sample else ''})
    return catalog


def discovery_terms(query):
    terms = query.lower()
    for phrase in ('是否存在', '有没有', '是否有', '能不能找到', '能找到', '请帮我找', '帮我找',
                   '相关的', '相关', '知识库', '产品信息', '产品资料', '产品说明', '产品', '资料',
                   '请问', '有吗', '吗', '是否', '支持', '可以', '的', '有', '找'):
        terms = terms.replace(phrase, '')
    return re.sub(r'[\s，。？！：；,.?!:;“”"()（）]+', '', terms)


def is_discovery_question(question):
    return ('知识库' in question or '产品资料' in question or '产品信息' in question
            or ('产品' in question and any(w in question for w in ('有没有', '是否有', '存在', '查找', '寻找'))))


def search_products(query, limit=6):
    """Return likely candidates; a low score never asserts product existence."""
    terms = discovery_terms(query)
    if not terms:
        return []
    catalog = product_catalog()
    if not catalog:
        return []
    can_fuzzy = len(terms) >= 2
    query_vec = VECTORIZER.transform([terms]).astype(np.float32).toarray()[0] if can_fuzzy else None
    grams = {terms[i:i+2] for i in range(len(terms)-1)} if can_fuzzy else set()
    with connect() as db:
        scored = []
        for product in catalog:
            names = [product['name'], product['id']]
            direct = any(name and name.lower() in query.lower() for name in names)
            direct = direct or any(alias and (terms == alias.lower() or
                                              (len(alias) >= 3 and alias.lower() in terms))
                                   for alias in product['aliases'])
            best = 1.0 if direct else 0.0
            if not direct and can_fuzzy:
                rows = db.execute('SELECT text,vector FROM chunks WHERE product_id=?', (product['id'],))
                for row in rows:
                    body = row['text'].split('\n', 1)[-1].lower()
                    body_grams = {body[i:i+2] for i in range(len(body)-1)}
                    coverage = len(grams & body_grams) / len(grams)
                    cosine = float(np.dot(query_vec, np.frombuffer(row['vector'], dtype=np.float32)))
                    best = max(best, 0.7 * cosine + 0.3 * coverage)
            if best >= 0.28:
                scored.append((best, product))
    scored.sort(key=lambda item: (-item[0], -item[1]['consultation_count'], item[1]['name']))
    return [{**product, 'match_score': round(score, 3)} for score, product in scored[:limit]]


def detect_product(question, explicit, prior):
    with connect() as db:
        products = db.execute('SELECT * FROM products').fetchall()
    for p in products:
        for name in [p['name'], p['id'], *json.loads(p['aliases'])]:
            if name and name.lower() in question.lower():
                return p['id']
    return explicit or prior


def scoped_model_facts(question, product_id, version):
    models = query.model_ids(question)
    field = query.field_name(question)
    if not models or not field:
        return None
    if len(models) > 1:
        return []
    # Use source text to recover model groups even in older indexes where the
    # headings and values were split apart or identical facts were deduplicated.
    with connect() as db:
        documents = db.execute('''SELECT d.id,d.source,d.category,t.text FROM documents d
            LEFT JOIN document_texts t ON t.document_id=d.id
            WHERE d.product_id=? AND d.version=? ORDER BY d.rowid''', (product_id, version)).fetchall()
        matches, related = [], []
        for doc in documents:
            texts = ([doc['text']] if doc['text'] is not None else [row['text'] for row in db.execute(
                'SELECT text FROM chunks WHERE document_id=? ORDER BY rowid', (doc['id'],))])
            facts = (fact for text in texts for fact in query.model_sections(text))
            for model, heading, fact in facts:
                label = fact.split(':', 1)[0].split('：', 1)[0].strip()
                generic = field in ('重量', '功率', '电压', '容量', '噪音', '噪声') and label.endswith(field)
                if model != models[0] or (label != field and not generic):
                    continue
                passage = f'{heading}\n{fact}'
                row = db.execute('SELECT id FROM chunks WHERE document_id=? AND text=?',
                                 (doc['id'], passage)).fetchone()
                target = matches if label == field else related
                target.append({'chunk_id': row['id'] if row else None, 'document_id': doc['id'],
                               'source': doc['source'], 'category': doc['category'], 'text': passage, 'score': 1.0})
        matches = matches or related
        return matches if len({item['text'].split('\n', 1)[-1] for item in matches}) <= 1 else []


def retrieve(question, product_id, version, top_k=4, *, db=None, details=None):
    if top_k < 1:
        return []
    scoped = scoped_model_facts(question, product_id, version)
    if scoped is not None:
        return scoped[:top_k]
    retriever = os.getenv('RAG_RETRIEVER', 'hash')
    if retriever == 'semantic':
        return semantic.retrieve(connect, question, product_id, version, top_k)
    with nullcontext(db) if db is not None else connect() as connection:
        product = connection.execute('SELECT name,aliases FROM products WHERE id=?', (product_id,)).fetchone()
        clean, alternatives = character_query(question, product_id, product)
        parameters = (product_id, version)
        sql = 'SELECT id,source,category,text,vector FROM chunks WHERE product_id=? AND version=?'
        if retriever == 'bm25':
            ids, fallback = bm25.candidates(connection, product_id, version, clean or question, alternatives, top_k)
            if ids:
                sql += ' AND id IN (' + ','.join('?' for _ in ids) + ') ORDER BY rowid'
                parameters += tuple(ids)
            if details is not None and not ids:
                details['retrieval_fallback'] = fallback or 'no_index_match'
        rows = connection.execute(sql, parameters).fetchall()
    if details is not None:
        details['retrieval_candidates'] = len(rows)
    if not rows:
        return []
    return rank_characters(question, clean, alternatives, rows, top_k)


def character_query(question, product_id, product):
    clean = question
    for name in [product_id, product['name'], *[a for a in json.loads(product['aliases']) if len(a) >= 3]] if product else [product_id]:
        clean = clean.replace(name, '')
    for phrase in ('追问', '最多', '多少', '支持', '是否', '可以', '有没有', '是什么', '怎么', '如何', '这个', '那个', '它', '请问', '限制为', '限制是'):
        clean = clean.replace(phrase, '')
    clean = re.sub(r'[\s，。？！：；,.?!:;0-9]+', '', clean)
    return clean, query.synonyms(question, product_id)


def rank_characters(question, clean, alternatives, rows, top_k):
    texts = [clean or question, *(clean + ' ' + term for term in alternatives)]
    query_vectors = VECTORIZER.transform(texts).astype(np.float32).toarray()
    query_grams = [{value[i:i+2] for i in range(len(value)-1) if ' ' not in value[i:i+2]}
                   for value in [clean, *texts[1:]]]
    scored, matches = [], []
    procedural = bool(query.PROCEDURE.search(question))
    for row in rows:
        vec = np.frombuffer(row['vector'], dtype=np.float32)
        body = row['text'].split('\n', 1)[-1]
        b = set(body[i:i+2] for i in range(len(body)-1))
        scores = [0.7 * float(np.dot(vector, vec)) + 0.3 * len(grams & b) / max(1, len(grams))
                  for vector, grams in zip(query_vectors, query_grams)]
        if procedural and row['text'].startswith(('操作步骤\n', '故障处理\n')):
            coverage = len(query_grams[0] & b) / max(1, len(query_grams[0]))
            if coverage >= 0.35:
                scores[0] = min(1.0, scores[0] + 0.12 * coverage)
        item = {'chunk_id': row['id'], 'source': row['source'], 'category': row['category'],
                'text': row['text'], 'score': round(scores[0], 4)}
        scored.append(item)
        for label, score in zip(alternatives, scores[1:]):
            if label in body:
                matches.append({**item, 'score': round(score, 4)})
    if alternatives and matches:
        # Preserve direct matches with the target concept; a generic similarity
        # score alone is insufficient to crowd out a precise terminology match.
        best = max(scored, key=lambda item: item['score'])
        direct_target = any(term in best['text'] for term in alternatives)
        if not direct_target or best['score'] < 0.16:
            merged = {item['chunk_id']: item for item in scored}
            for item in matches:
                if item['score'] > merged[item['chunk_id']]['score']:
                    merged[item['chunk_id']] = item
            scored = list(merged.values())
    return sorted(scored, key=lambda x: x['score'], reverse=True)[:top_k]


def faq_terms(question, product_name):
    terms = question.lower().replace(product_name.lower(), '')
    terms = re.sub(r'\b\d+(?:\.\d+)*\b', '', terms)
    for phrase in ('请问', '是否', '可以', '能否', '怎么', '如何', '多少', '最多', '支持',
                   '这个', '那个', '它', '是什么', '怎么办', '吗', '呢', '的'):
        terms = terms.replace(phrase, '')
    return re.sub(r'[\s，。？！：；,.?!:;“”"()（）]+', '', terms) or question.lower()


def faq_intent(question):
    if any(term in question for term in ('价格', '售价', '收费', '多少钱')):
        return '价格'
    if any(term in question for term in ('保修', '质保')):
        return '售后保修'
    if any(term in question for term in ('兼容', '适配')):
        return '兼容性'
    if any(term in question for term in ('适用', '场景', '适合')):
        return '适用场景'
    if any(term in question for term in ('最多', '多少', '上限', '限制', '容量', '大小')):
        return '参数限制'
    if any(term in question for term in ('忘记密码', '报错', '故障', '无法', '失败')):
        return '故障处理'
    if any(term in question for term in ('怎么', '如何', '步骤', '怎样')):
        return '操作步骤'
    if any(term in question for term in ('支持', '能否', '可以', '能不能')):
        return '功能支持'
    return None


def list_faqs(product_id, version, limit=12):
    limit = max(1, min(int(limit), 500))
    with connect() as db:
        rows = db.execute('''SELECT question, answer, category, source, kind FROM faqs
                             WHERE product_id=? AND version=?
                             ORDER BY CASE WHEN kind='manual' THEN 0 WHEN kind='explicit' THEN 1
                                  WHEN kind IN ('parameter','procedure','troubleshooting','trained') THEN 2
                                  WHEN kind='auto' THEN 3 ELSE 4 END, rowid
                             LIMIT ?''', (product_id, version, limit)).fetchall()
    return [dict(row) for row in rows]


def supplement_gap(gap_id, product_id, version, question, answer):
    """Save a reviewed Q/A and optionally close a matching knowledge gap."""
    product_id, version = product_id.strip(), version.strip()
    question, answer = question.strip(), answer.strip()
    if not product_id or not version or len(version) > 40:
        raise ValueError('请选择产品并填写有效版本')
    if not 2 <= len(question) <= 500 or not 2 <= len(answer) <= 4000:
        raise ValueError('问题需为 2–500 字，答案需为 2–4000 字')
    with connect() as db:
        gap = (db.execute('SELECT * FROM unanswered WHERE id=? AND resolved_at IS NULL',
                          (gap_id,)).fetchone() if gap_id is not None else None)
        if gap_id is not None and not gap:
            raise KeyError('知识缺口不存在或已解决')
        product = db.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone()
        if not product:
            raise ValueError('所选产品不存在')
        if gap and gap['product_id'] and product_id != gap['product_id']:
            raise ValueError('补充答案必须写入缺口所属的产品')
        if gap and gap['version'] and version != gap['version']:
            raise ValueError('补充答案必须写入缺口所属的版本')
        if gap and gap['product_id'] and faq_terms(question, product['name']) != faq_terms(gap['question'], product['name']):
            raise ValueError('补充问题需与当前知识缺口一致')
        existing = db.execute('SELECT * FROM faqs WHERE product_id=? AND version=?',
                              (product_id, version)).fetchall()
        for row in existing:
            if faq_terms(row['question'], product['name']) == faq_terms(question, product['name']):
                if row['answer'].strip() != answer:
                    raise ValueError('该产品版本已有同一问题的不同答案，请先核对现有资料')
                faq_id, doc_id = row['id'], row['document_id']
                break
        else:
            doc_id, faq_id = uuid.uuid4().hex, uuid.uuid4().hex
            source = '人工补充'
            timestamp = now()
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                       (doc_id, product_id, version, '人工 FAQ', '人工补充：' + question[:60],
                        source, timestamp, json.dumps({'manual': True}, ensure_ascii=False)))
            db.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex, doc_id, product_id, version, '人工 FAQ', source,
                        answer, VECTORIZER.transform([answer]).astype(np.float32).toarray()[0].tobytes()))
            db.execute('INSERT INTO faqs VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (faq_id, doc_id, product_id, version, '人工 FAQ', source,
                        question, answer, 'manual',
                        VECTORIZER.transform([faq_terms(question, product['name'])])
                        .astype(np.float32).toarray()[0].tobytes()))
        resolved = (db.execute('''UPDATE unanswered SET resolved_at=?
                                 WHERE resolved_at IS NULL AND question=?
                                 AND product_id IS ? AND version IS ?''',
                               (now(), gap['question'], gap['product_id'], gap['version'])).rowcount
                    if gap else 0)
    return {'document_id': doc_id, 'faq_id': faq_id, 'resolved_count': resolved,
            'product_id': product_id, 'version': version}


def match_faq(question, product_id, version):
    with connect() as db:
        product = db.execute('SELECT name,id,aliases FROM products WHERE id=?', (product_id,)).fetchone()
        rows = db.execute('SELECT * FROM faqs WHERE product_id=? AND version=?',
                          (product_id, version)).fetchall()
    if not product or not rows:
        return None
    terms = faq_terms(question, product['name'])
    query_vec = VECTORIZER.transform([terms]).astype(np.float32).toarray()[0]
    grams = {terms[i:i+2] for i in range(len(terms)-1)}
    product_words = set(re.findall(r'[a-z]{2,}', product['id'].lower() + ' ' + product['name'].lower() +
                                   ' ' + ' '.join(json.loads(product['aliases'])).lower()))
    required_words = [word.lower() for word in re.findall(r'[A-Za-z]{2,}', question)
                      if word.lower() not in product_words]
    intent = faq_intent(question)
    candidates = []
    for row in rows:
        if intent and row['category'] not in (intent, '文档 FAQ', '人工 FAQ'):
            continue
        evidence = (row['question'] + ' ' + row['answer']).lower()
        if any(word not in evidence for word in required_words):
            continue
        faq_clean = faq_terms(row['question'], product['name'])
        faq_grams = {faq_clean[i:i+2] for i in range(len(faq_clean)-1)}
        coverage = len(grams & faq_grams) / max(1, len(grams))
        cosine = float(np.dot(query_vec, np.frombuffer(row['vector'], dtype=np.float32)))
        score = 0.7 * cosine + 0.3 * coverage
        if score >= 0.38 and coverage >= 0.25:
            candidates.append((score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    score, row = candidates[0]
    same_question = [item for _, item in candidates
                     if faq_terms(item['question'], product['name']) == faq_terms(row['question'], product['name'])]
    conflict = len({item['answer'].strip() for item in same_question}) > 1
    return {'answer': row['answer'], 'score': round(score, 4), 'conflict': conflict,
            'source': {'chunk_id': row['id'], 'source': row['source'], 'category': row['category'],
                       'text': row['answer'], 'score': round(score, 4)}}


def llm_answer(question, sources, *, model_details=None, history=()):
    config = llm_config.effective(DB_PATH)
    endpoint, key, model = config['base_url'], config['api_key'], config['model']
    if not llm_config.enabled(DB_PATH):
        return None
    prompt = answer_prompt_config()['prompt']
    context = '\n\n'.join(f"[{i+1}] {s['text']}" for i, s in enumerate(sources))
    current = f'问题：{question}\n\n资料：\n{context}'
    if history:
        current += '\n\n以上为本轮资料。此前消息仅用于理解指代，事实依据仅限本轮编号资料。'
    payload = json.dumps({'model': model, 'temperature': 0, 'messages': [
        {'role': 'system', 'content': prompt}, *history,
        {'role': 'user', 'content': current}
    ]}).encode()
    request = urllib.request.Request(endpoint + '/chat/completions', data=payload,
                                     headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.load(response)
        answer = result['choices'][0]['message']['content'].strip()
    if model_details is not None and isinstance(result.get('model'), str):
        model_details['model_used'] = result['model'][:200]
    return answer


def enough_evidence(question, sources, threshold, retriever, product_id):
    if not sources:
        return False
    top = sources[0]
    text = query.normalize(top['text']).casefold()
    field = query.field_name(question)
    anchors = (*query.synonyms(question, product_id), *((field,) if field else ()))
    # Only relax short-fact gating if the candidate names the requested fact.
    body = top['text'].split('\n', 1)[-1].casefold()
    if (retriever in ('hash', 'bm25') and threshold == 0.16 and len(body) <= 36
            and any(re.match(re.escape(anchor.casefold()) + r'\s*[:：]', body) for anchor in anchors)):
        threshold = 0.12
    if top['score'] < threshold:
        return False
    if not query.contains_requested_fact(question, text):
        return False
    if any(term.casefold() not in text for term in re.findall(r'[A-Za-z]{3,}', question)
           if term.casefold() not in ('nova', 'notes', 'orbit', 'tasks')):
        return False
    topic = query.required_feature(question)
    if len(topic) < 4 or topic.startswith(('哪些', '哪种', '什么', '多少', '多久', '如何', '怎么')):
        return True
    topic_grams = {topic[i:i+2] for i in range(len(topic) - 1)}
    compact = re.sub(r'\W+', '', text)
    return sum(gram in compact for gram in topic_grams) >= max(1, (len(topic_grams) + 2) // 3)


def generation_evidence(sources, threshold, aggregate):
    limit = 6 if aggregate else 3
    floor = max(threshold, sources[0]['score'] * 0.55)
    selected, texts = [], []
    for source in sources:
        text = query.normalize(source['text']).strip().casefold()
        if not text or any(text in other or other in text for other in texts):
            continue
        if aggregate and selected and source['score'] < floor:
            continue
        selected.append(source)
        texts.append(text)
        if len(selected) == limit:
            break
    return selected


def handoff(db, session_id, question, product_id, version, reason):
    history = db.execute('SELECT role,text FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 6',
                         (session_id,)).fetchall()
    summary = f'产品：{product_id or "未确认"}；版本：{version or "未确认"}\n原因：{reason}\n当前问题：{question}\n近期对话：\n'
    summary += '\n'.join(f"{x['role']}：{x['text']}" for x in reversed(history))
    ticket_id = uuid.uuid4().hex[:12]
    db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)', (ticket_id, session_id, summary, 'open', now()))
    return ticket_id


def conversational_answer(question, answer, status, product_name=None, version=None):
    """Add conversational framing without changing the factual answer or source."""
    if status == 'answered':
        fact = answer.strip()
        if fact.startswith('#') and '\n' in fact:
            fact = fact.split('\n', 1)[1].strip()
        fact = re.sub(r'^[•●]\s*', '', fact)
        if any(word in question for word in ('怎么', '如何', '步骤', '怎样', '怎么办')):
            return '可以按资料中的说明这样处理：\n' + fact
        if any(word in question for word in ('最多', '多少', '上限', '限制', '大小', '容量')):
            return '我查到的具体限制是：' + fact
        if any(word in question for word in ('支持', '能否', '可以', '能不能')):
            return '关于这个功能，资料说明：' + fact
        return '我查到的说明是：' + fact
    if status == 'no_answer':
        scope = f'{product_name} {version}'.strip() if product_name else '当前产品'
        return f'抱歉，我暂时没在{scope}的资料里找到这个问题的明确答案。你可以换个更具体的问法，或在知识库管理中补充资料。'
    if status == 'handoff':
        return '这件事需要人工确认，我已为你记录。' if '已为你生成转人工记录' in answer else answer
    return answer


def ask(question, session_id=None, product_id=None, version=None, *, include_retrieved=False):
    start = time.monotonic()
    question = question.strip()
    if not question or len(question) > 1000:
        raise ValueError('问题长度需为 1–1000 字')
    retriever = os.getenv('RAG_RETRIEVER', 'hash')
    answer_mode = llm_config.effective(DB_PATH)['answer_mode']
    if retriever not in ('hash', 'bm25', 'semantic') or answer_mode not in ('auto', 'extractive', 'llm'):
        raise ValueError('RAG_RETRIEVER 应为 hash/bm25/semantic，RAG_ANSWER_MODE 应为 auto/extractive/llm')
    minimum_score = float(os.getenv('RAG_MIN_SCORE', '0.35' if retriever == 'semantic' else '0.16'))
    if not np.isfinite(minimum_score) or not 0 <= minimum_score <= 1:
        raise ValueError('RAG_MIN_SCORE 必须为 0 到 1 的有限数值')
    routed = query.needs_handoff(question)
    discovery = is_discovery_question(question)
    if retriever == 'semantic' and not (routed or discovery):
        # Model calls and index writes must finish before the conversation write lock.
        semantic.index_chunks(connect)
    generation_mode, generation_error = 'rule', None
    model_details = {}
    retrieval_details = {}
    session_id = session_id or uuid.uuid4().hex
    matched_products = search_products(question) if discovery and not routed else []
    if discovery:
        version = None
    with connect() as db:
        old = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
        if not old:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (session_id, None, None, now()))
        selected_product = product_id
        product_id = (None if discovery else
                      detect_product(question, product_id, old['product_id'] if old else None))
        if selected_product and product_id != selected_product:
            version = None
        version = version or (old['version'] if old and old['product_id'] == product_id else None)
        if product_id and not version:
            with connect() as lookup:
                row = lookup.execute('SELECT version FROM documents WHERE product_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1',
                                     (product_id,)).fetchone()
            version = row['version'] if row else None
        same_scope = bool(product_id and version and old and old['product_id'] == product_id and old['version'] == version)
        history_rows = []
        if same_scope and query.FOLLOWUP.search(question) and not routed:
            recent = db.execute('''SELECT role,text,status,product_id,version FROM messages
                                   WHERE session_id=? ORDER BY id DESC LIMIT 4''', (session_id,))
            for row in recent:
                if row['product_id'] != product_id or row['version'] != version or row['status'] == 'handoff':
                    break
                history_rows.append(row)
        previous = next((row['text'] for row in history_rows if row['role'] == 'user'), '')
        rewritten, ambiguous = query.rewrite(question, previous) if same_scope or not old else (question, False)
        previous_models = query.model_ids(previous)
        current_models = query.model_ids(question)
        if history_rows and current_models and current_models != previous_models:
            history_rows = []
        elif history_rows and len(previous_models) == 1 and not current_models and query.field_name(rewritten):
            rewritten = previous_models[0] + ' ' + rewritten
        if not discovery:
            db.execute('UPDATE sessions SET product_id=?,version=? WHERE id=?', (product_id, version, session_id))
        db.execute('INSERT INTO messages(session_id,role,text,created_at,product_id,version) VALUES(?,?,?,?,?,?)',
                   (session_id, 'user', question, now(), product_id, version))
        eligible = product_id and version and not (discovery or routed or ambiguous)
        aggregate = bool(query.AGGREGATE.search(rewritten))
        depth = 8 if aggregate and answer_mode != 'extractive' else (5 if include_retrieved else 4)
        options = {'db': db, 'details': retrieval_details} if retriever == 'bm25' else {}
        sources = retrieve(rewritten, product_id, version, top_k=depth, **options) if eligible else []
        retrieved = sources[:5]
        model_field = query.model_ids(rewritten) and query.field_name(rewritten)
        faq = match_faq(rewritten, product_id, version) if eligible and not model_field else None
        if faq and not faq['conflict'] and not query.contains_requested_fact(rewritten, query.normalize(faq['answer']).casefold()):
            faq = None
        top = sources[0]['score'] if sources else 0
        ticket_id = None
        if routed:
            status = 'handoff'
            answer = '此问题需要人工客服处理，已为你生成转人工记录。'
            ticket_id = handoff(db, session_id, question, product_id, version, '敏感业务或用户要求人工')
            sources = []
        elif discovery:
            status = 'product_found' if matched_products else 'no_product'
            answer = ('找到可能相关的产品知识库，请选择产品后继续提问。' if matched_products else
                      '未找到匹配的产品知识库，请联系人工客服。')
            sources = []
        elif not product_id:
            matched_products = search_products(question)
            status = 'product_found' if matched_products else 'no_product'
            answer = ('找到可能相关的产品知识库，请选择产品后继续提问。' if matched_products else
                      '未找到匹配的产品知识库，请联系人工客服。')
            sources = []
        elif not version:
            status = 'no_product'
            answer = '该产品尚无已导入的知识资料，请联系人工客服。'
            sources = []
        elif ambiguous:
            status = 'no_answer'
            answer = '请补充想了解的参数或操作，例如容量、功率或连接步骤。'
        elif faq and faq['conflict']:
            status = 'handoff'
            answer = '产品资料对这个问题给出了冲突答案，请联系人工客服确认。'
            ticket_id = handoff(db, session_id, question, product_id, version, '同一常见问题存在冲突答案')
            sources = []
        elif faq and answer_mode == 'auto':
            status = 'answered'
            answer = faq['answer']
            sources = [faq['source']]
            top = faq['score']
            generation_mode = 'faq'
        elif not enough_evidence(rewritten, sources, minimum_score, retriever, product_id):
            status = 'no_answer'
            answer = '现有产品资料中暂未找到相关说明，请补充问题或联系人工客服。'
            sources = []
        else:
            status = 'answered'
            selected = generation_evidence(sources, minimum_score, aggregate)
            history = [{'role': row['role'], 'content': row['text'][:700]} for row in reversed(history_rows)]
            try:
                if answer_mode == 'extractive':
                    generated = None
                else:
                    options = {'model_details': model_details} if include_retrieved else {}
                    if history:
                        options['history'] = history
                    generated = llm_answer(rewritten, selected, **options)
            except Exception as error:
                generated = None
                generation_error = ('HTTP' + str(error.code) if isinstance(error, urllib.error.HTTPError)
                                    else type(error).__name__)
            # Offline mode presents a verbatim retrieved passage, never invented facts.
            answer = generated or sources[0]['text']
            generation_mode = 'llm' if generated else 'extractive'
            sources = selected if generated else sources[:3]
            if answer_mode == 'llm' and not generated:
                status = 'no_answer'
                generation_mode = 'error'
                generation_error = generation_error or 'LLMNotConfiguredOrEmpty'
                answer = llm_config.error_message(generation_error)
            elif generated and (NO_ANSWER in generated or any(not 1 <= int(i) <= len(sources)
                    for i in re.findall(r'\[(\d{1,9})\]', generated))):
                status = 'no_answer'
                answer = NO_ANSWER + '。'
        if status in ('no_answer', 'no_product'):
            db.execute('INSERT INTO unanswered(session_id,question,product_id,version,created_at) VALUES(?,?,?,?,?)',
                       (session_id, question, product_id, version, now()))
        message_id = db.execute('INSERT INTO messages(session_id,role,text,status,created_at,product_id,version) VALUES(?,?,?,?,?,?,?)',
                                (session_id, 'assistant', answer, status, now(), product_id, version)).lastrowid
        product_name_row = db.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone() if product_id else None
        display_answer = (answer if generation_mode == 'error' or ambiguous else
                          conversational_answer(question, answer, status,
                                               product_name_row['name'] if product_name_row else None, version))
        suggested_questions = []
        if status == 'answered' and product_name_row:
            current_key = faq_terms(question, product_name_row['name'])
            current_category = faq['source']['category'] if faq else None
            candidates = db.execute('''SELECT question,answer,category FROM faqs
                                       WHERE product_id=? AND version=? ORDER BY rowid LIMIT 80''',
                                    (product_id, version)).fetchall()
            candidates = sorted(candidates, key=lambda row: row['category'] != current_category)
            seen_answers = {answer.strip()}
            for candidate in candidates:
                if (faq_terms(candidate['question'], product_name_row['name']) == current_key
                        or candidate['answer'].strip() in seen_answers):
                    continue
                suggested_questions.append(candidate['question'])
                seen_answers.add(candidate['answer'].strip())
                if len(suggested_questions) == 2:
                    break
    return {'session_id': session_id, 'product_id': product_id, 'version': version,
            'message_id': message_id,
            'rewritten_question': rewritten, 'answer': answer, 'display_answer': display_answer,
            'suggested_questions': suggested_questions, 'status': status,
            'confidence': round(top, 4), 'sources': sources if status == 'answered' else [],
            'matched_products': matched_products, 'handoff_id': ticket_id,
            'retriever': retriever, 'generation_mode': generation_mode,
            'generation_error': generation_error,
            'latency_ms': round((time.monotonic()-start)*1000),
            **retrieval_details, **({'retrieved': retrieved, **model_details} if include_retrieved else {})}
