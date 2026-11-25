"""Semantic embeddings with durable vectors and a scope-filtered FAISS index.

Hash vectors remain a separate baseline. Models/configurations never share vectors.
"""
from contextlib import closing
import hashlib
import json
import os
import urllib.request
from functools import lru_cache
from pathlib import Path
from threading import Lock

import numpy as np

from . import postgres

os.environ.setdefault('HF_HOME', str(Path(__file__).resolve().parents[1] / '.cache/huggingface'))
model_lock = Lock()


class EmbeddingNotReady(RuntimeError):
    """The configured query embedding model could not be loaded from disk."""


def config():
    provider = os.getenv('EMBEDDING_PROVIDER', 'local')
    if provider not in ('local', 'api'):
        raise ValueError('EMBEDDING_PROVIDER 必须为 local 或 api')
    model = os.getenv('EMBEDDING_MODEL', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')
    endpoint = os.getenv('EMBEDDING_BASE_URL', '').rstrip('/') if provider == 'api' else ''
    identity = hashlib.sha256(json.dumps([provider, model, endpoint]).encode()).hexdigest()
    return provider, model, endpoint, identity


def load_sentence_transformer(name, *, local_files_only):
    import torch
    # Keep torch/FAISS OpenMP pools bounded on small classroom machines.
    torch.set_num_threads(int(os.getenv('RAG_EMBEDDING_THREADS', '1')))
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    from sentence_transformers import SentenceTransformer
    model_path = name
    if local_files_only and not Path(name).is_dir():
        from huggingface_hub import snapshot_download

        # Pass a directory so tokenizer initialization cannot request Hub metadata.
        model_path = snapshot_download(name, local_files_only=True,
                                       cache_dir=os.getenv('SENTENCE_TRANSFORMERS_HOME'))
    return SentenceTransformer(model_path, local_files_only=local_files_only)


@lru_cache(maxsize=2)
def cached_local_model(name):
    return load_sentence_transformer(name, local_files_only=True)


def local_model(name):
    with model_lock:
        try:
            return cached_local_model(name)
        except ImportError as error:
            raise EmbeddingNotReady('缺少本地 Embedding 依赖，请通过项目启动脚本安装依赖后重试。') from error
        # Some Transformers releases raise AttributeError on an incomplete checkpoint cache.
        except (OSError, ValueError, AttributeError) as error:
            raise EmbeddingNotReady('本地 Embedding 模型尚未就绪。请稍后重试，或在终端运行 python run_server.py --setup-embedding。') from error


def prepare_local_model():
    provider, model, _, _ = config()
    if provider != 'local':
        return
    os.environ.setdefault('HF_HUB_ETAG_TIMEOUT', '5')
    os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '25')
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    try:
        local_model(model)
    except EmbeddingNotReady:
        print(f'正在下载本地 Embedding 模型 {model}，下载完成前网页仍可查看和管理资料。', flush=True)
        load_sentence_transformer(model, local_files_only=False)
    print('本地 Embedding 模型已就绪。', flush=True)


def normalize(values):
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim != 2 or not matrix.shape[1] or not np.isfinite(matrix).all():
        raise ValueError('Embedding 必须为有限数值的二维矩阵')
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if (norms == 0).any():
        raise ValueError('Embedding 不能为零向量')
    return np.ascontiguousarray(matrix / norms)


def embed(texts):
    provider, model, endpoint, _ = config()
    if provider == 'local':
        return normalize(local_model(model).encode(texts, show_progress_bar=False))
    key = os.getenv('EMBEDDING_API_KEY', '')
    if not endpoint or not key:
        raise ValueError('API Embedding 需要 EMBEDDING_BASE_URL 和 EMBEDDING_API_KEY')
    request = urllib.request.Request(endpoint + '/embeddings',
        data=json.dumps({'model': model, 'input': texts, 'encoding_format': 'float'}).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.load(response)['data']
    data.sort(key=lambda item: item['index'])
    if [item['index'] for item in data] != list(range(len(texts))):
        raise ValueError('Embedding 响应数量或序号不匹配')
    return normalize([item['embedding'] for item in data])


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS semantic_vectors(
        chunk_id TEXT NOT NULL, identity TEXT NOT NULL, content_hash TEXT NOT NULL,
        vector BYTEA NOT NULL, dimension INTEGER NOT NULL,
        PRIMARY KEY(chunk_id,identity),
        FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE)''')


def index_chunks(connect, product_id=None, version=None):
    """Embed missing/changed chunks in batches, outside the write transaction."""
    _, _, _, identity = config()
    with closing(connect()) as db, db:
        if not postgres.enabled():
            ensure_schema(db)
        rows = db.execute('''SELECT c.id,c.text,s.content_hash FROM chunks c
            LEFT JOIN semantic_vectors s ON s.chunk_id=c.id AND s.identity=?
            WHERE (? IS NULL OR c.product_id=?) AND (? IS NULL OR c.version=?)
            ORDER BY c.id''', (identity, product_id, product_id, version, version)).fetchall()
    pending = []
    for row in rows:
        digest = hashlib.sha256(row['text'].encode()).hexdigest()
        if row['content_hash'] != digest:
            pending.append((row, digest))
    for start in range(0, len(pending), 32):
        batch = pending[start:start + 32]
        vectors = embed([row['text'] for row, _ in batch])
        if len(vectors) != len(batch):
            raise ValueError('Embedding 数量不匹配')
        with closing(connect()) as db, db:
            for (row, digest), vector in zip(batch, vectors):
                # Confirm the stored text still matches the completed embedding.
                current = 'SELECT 1 FROM chunks WHERE id=? AND text=?'
                if postgres.enabled():
                    current += ' FOR SHARE'
                if db.execute(current, (row['id'], row['text'])).fetchone():
                    db.execute('INSERT INTO semantic_vectors VALUES(?,?,?,?,?) ON CONFLICT(chunk_id,identity) DO UPDATE SET content_hash=excluded.content_hash,vector=excluded.vector,dimension=excluded.dimension',
                               (row['id'], identity, digest, vector.tobytes(), len(vector)))
    return {'chunks': len(rows), 'embedded': len(pending), 'identity': identity,
            'model': config()[1]}


def retrieve(connect, question, product_id, version, top_k):
    if top_k < 1:
        return []
    import faiss
    faiss.omp_set_num_threads(int(os.getenv('RAG_EMBEDDING_THREADS', '1')))
    index_chunks(connect, product_id, version)
    with closing(connect()) as db, db:
        rows = db.execute('''SELECT c.id,c.source,c.category,c.text,s.vector AS semantic_vector,s.dimension
            FROM chunks c JOIN semantic_vectors s ON s.chunk_id=c.id
            WHERE c.product_id=? AND c.version=? AND s.identity=? ORDER BY c.id''',
            (product_id, version, config()[3])).fetchall()
    if not rows:
        return []
    dimensions = {row['dimension'] for row in rows}
    query = embed([question])
    if dimensions != {query.shape[1]}:
        raise ValueError('Embedding 维度改变，请更换 EMBEDDING_MODEL 标识并重新建索引')
    matrix = np.stack([np.frombuffer(row['semantic_vector'], dtype=np.float32) for row in rows])
    index = faiss.IndexFlatIP(query.shape[1])
    index.add(matrix)
    scores, ids = index.search(query, min(top_k, len(rows)))
    return [{'chunk_id': rows[i]['id'], 'source': rows[i]['source'],
             'category': rows[i]['category'], 'text': rows[i]['text'],
             'score': round(float(score), 4)} for score, i in zip(scores[0], ids[0])]
