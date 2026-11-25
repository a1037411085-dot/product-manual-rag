"""Synchronize PostgreSQL's semantic vectors with a remote Chroma collection."""
import atexit
from contextlib import closing
from functools import lru_cache
import hashlib
import json
import os
import re

import numpy as np

from . import postgres, query, semantic

BATCH_SIZE = 128


def ensure_schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS chroma_scopes(
        collection TEXT NOT NULL, product_id TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL,
        PRIMARY KEY(collection, product_id, version))''')


def configured():
    return bool(os.getenv('CHROMA_HOST') or (
        os.getenv('CHROMA_API_KEY') and os.getenv('CHROMA_TENANT') and os.getenv('CHROMA_DATABASE')))


@lru_cache(maxsize=4)
def client(host, port, ssl, tenant, database, key):
    import chromadb
    if host:
        remote = chromadb.HttpClient(host=host, port=port, ssl=ssl, tenant=tenant or 'default_tenant',
                                     database=database or 'default_database',
                                     headers={'Authorization': 'Bearer ' + key} if key else None)
    else:
        if not all((tenant, database, key)):
            raise ValueError('请设置 CHROMA_HOST，或配置 Chroma Cloud 的 API Key、Tenant、Database。')
        remote = chromadb.CloudClient(tenant=tenant, database=database, api_key=key)
    close = getattr(remote, 'close', None)
    if close:
        atexit.register(close)
    return remote


def collection_name():
    identity = semantic.config()[3]
    prefix = os.getenv('CHROMA_COLLECTION')
    if not prefix:
        from psycopg.conninfo import conninfo_to_dict
        dsn = conninfo_to_dict(os.environ['RAG_DATABASE_URL'])
        prefix = [dsn.get(key, '') for key in ('host', 'port', 'dbname')]
    scope = json.dumps([prefix, postgres.schema_name(), identity], ensure_ascii=False)
    return 'rag-' + hashlib.sha256(scope.encode()).hexdigest()[:32]


def chroma_client():
    return client(os.getenv('CHROMA_HOST'), int(os.getenv('CHROMA_PORT', '8001')),
                  os.getenv('CHROMA_SSL') == '1', os.getenv('CHROMA_TENANT'),
                  os.getenv('CHROMA_DATABASE'), os.getenv('CHROMA_API_KEY'))


def collection():
    return chroma_client().get_or_create_collection(name=collection_name(), embedding_function=None)


def scope_filter(product_id, version):
    return {'$and': [{'product_id': product_id}, {'version': version}]}


def batches(items):
    for offset in range(0, len(items), BATCH_SIZE):
        yield items[offset:offset + BATCH_SIZE]


def sync_scope(connect, coll, product_id, version, *, force=False):
    col_id = str(coll.id)
    identity = semantic.config()[3]
    scope = (product_id, version)
    # Session-scoped lock serializes competing synchronizers, without an open write transaction.
    lock_key = json.dumps([col_id, *scope], ensure_ascii=False)
    with closing(connect()) as db:
        db.execute("SELECT pg_advisory_lock(hashtextextended(current_schema() || ':' || ?, 0))", (lock_key,))
        stats = semantic.index_chunks(connect, *scope)
        rows = db.execute('''SELECT s.chunk_id, s.content_hash
                             FROM semantic_vectors s JOIN chunks c ON c.id=s.chunk_id
                             WHERE s.identity=? AND c.product_id=? AND c.version=? ORDER BY s.chunk_id''',
                          (identity, *scope)).fetchall()
        local = {row['chunk_id']: row['content_hash'] for row in rows}
        digest = hashlib.sha256(json.dumps(local, sort_keys=True).encode()).hexdigest()
        current = db.execute('SELECT digest FROM chroma_scopes WHERE collection=? AND product_id=? AND version=?',
                             (col_id, *scope)).fetchone()
        if not force and current and current[0] == digest:
            return {**stats, 'upserted': 0, 'deleted': 0}
        remote = {}
        offset = 0
        while True:
            page = coll.get(where=scope_filter(*scope), limit=BATCH_SIZE, offset=offset, include=['metadatas'])
            remote.update((key, metadata['content_hash'])
                          for key, metadata in zip(page['ids'], page['metadatas']))
            if len(page['ids']) < BATCH_SIZE:
                break
            offset += BATCH_SIZE
        changed = [chunk_id for chunk_id, value in local.items() if remote.get(chunk_id) != value]
        removed = sorted(remote.keys() - local.keys())
        sent = 0
        for batch in batches(changed):
            placeholders = ','.join('?' for _ in batch)
            vectors = db.execute(f'''SELECT chunk_id, content_hash, vector FROM semantic_vectors
                                     WHERE identity=? AND chunk_id IN ({placeholders})''',
                                 (identity, *batch)).fetchall()
            if vectors:
                coll.upsert(ids=[row['chunk_id'] for row in vectors],
                            embeddings=[np.frombuffer(row['vector'], dtype=np.float32).tolist() for row in vectors],
                            metadatas=[{'product_id': product_id, 'version': version, 'content_hash': row['content_hash']}
                                       for row in vectors])
                sent += len(vectors)
        for batch in batches(removed):
            coll.delete(ids=batch)
        db.execute('''INSERT INTO chroma_scopes VALUES(?,?,?,?)
                      ON CONFLICT(collection,product_id,version) DO UPDATE SET digest=excluded.digest''',
                   (col_id, *scope, digest))
        return {**stats, 'upserted': sent, 'deleted': len(removed)}


def reindex(connect, product_id=None, version=None):
    coll = collection()
    with closing(connect()) as db:
        rows = db.execute('''SELECT product_id, version FROM chunks
                             UNION SELECT product_id, version FROM chroma_scopes WHERE collection=?
                             ORDER BY 1,2''', (str(coll.id),)).fetchall()
    stats = {key: 0 for key in ('chunks', 'embedded', 'upserted', 'deleted')}
    for candidate, revision in rows:
        if (product_id is None or candidate == product_id) and (version is None or revision == version):
            result = sync_scope(connect, coll, candidate, revision, force=True)
            for key in stats:
                stats[key] += result[key]
    return {**stats, 'collection': coll.name, 'indexed': coll.count()}


def rank_hits(question, hits):
    field = query.field_name(question) if query.FACT_ASK.search(question) else None
    if not field:
        return sorted(hits, key=lambda hit: hit['score'], reverse=True)
    label = re.compile(r'^[ \t]*' + re.escape(field) + r'[ \t]*:[ \t]*(?![/—-]+[ \t]*$)\S', re.MULTILINE)
    return sorted(hits, key=lambda hit: (bool(label.search(query.normalize(hit['text']))), hit['score']),
                  reverse=True)


def retrieve(connect, question, product_id, version, top_k):
    coll = collection()
    stats = sync_scope(connect, coll, product_id, version)
    if not stats['chunks']:
        return []
    vector = semantic.embed([question])[0]
    result = coll.query(query_embeddings=[vector.tolist()], n_results=top_k, where=scope_filter(product_id, version),
                        include=['metadatas'])
    ids = result['ids'][0]
    if not ids:
        return []
    placeholders = ','.join('?' for _ in ids)
    with closing(connect()) as db:
        rows = db.execute(f'''SELECT c.id,c.source,c.category,c.text,s.content_hash,s.vector
                              FROM chunks c JOIN semantic_vectors s ON s.chunk_id=c.id
                              WHERE c.product_id=? AND c.version=? AND s.identity=? AND c.id IN ({placeholders})''',
                          (product_id, version, semantic.config()[3], *ids)).fetchall()
    by_id = {row['id']: row for row in rows}
    hits = []
    for key, metadata in zip(ids, result['metadatas'][0]):
        row = by_id.get(key)
        if not row or row['content_hash'] != metadata['content_hash']:
            continue
        if hashlib.sha256(row['text'].encode()).hexdigest() != row['content_hash']:
            continue
        value = np.frombuffer(row['vector'], dtype=np.float32)
        score = round(float(np.dot(vector, value)), 4)
        hits.append({'chunk_id': key, 'source': row['source'], 'category': row['category'],
                     'text': row['text'], 'score': score})
    return rank_hits(question, hits)


def delete_collection():
    chroma_client().delete_collection(collection_name())
