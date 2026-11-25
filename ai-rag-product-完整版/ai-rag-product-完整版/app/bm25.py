"""Optional SQLite FTS5 candidate index for the character reranker."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from itertools import islice

from .query import normalize


CANDIDATE_LIMIT = 30
# Pre-tokenize Han text so two-character terms such as 童锁 are indexable.
TOKEN_RUN = re.compile(r'[\u3400-\u9fff]+|[a-z0-9]+', re.I)


def terms(text):
    for run in TOKEN_RUN.findall(normalize(text).casefold()):
        if '\u3400' <= run[0] <= '\u9fff' and len(run) > 1:
            yield from (run[i:i+2] for i in range(len(run)-1))
        else:
            yield run


def scope_token(product_id, version):
    return 's' + hashlib.sha256(json.dumps([product_id, version]).encode()).hexdigest()


def schema_exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='rag_bm25_pending'").fetchone() is not None


def available(db):
    return bool(db.execute("SELECT sqlite_compileoption_used('ENABLE_FTS5')").fetchone()[0])


def dirty(db):
    return not schema_exists(db) or db.execute('SELECT 1 FROM rag_bm25_pending LIMIT 1').fetchone() is not None


def status(db):
    ready = schema_exists(db)
    pending = db.execute('SELECT count(*) FROM rag_bm25_pending').fetchone()[0] if ready else None
    return {'available': available(db), 'built': ready, 'pending_scopes': pending}


def create_schema(db):
    db.execute('CREATE VIRTUAL TABLE rag_bm25 USING fts5(scope, tokens, chunk_id UNINDEXED)')
    db.execute("INSERT INTO rag_bm25(rag_bm25,rank) VALUES('rank','bm25(0.0,1.0,0.0)')")
    db.execute('CREATE TABLE rag_bm25_pending(product_id TEXT, version TEXT, PRIMARY KEY(product_id,version))')
    db.execute('INSERT INTO rag_bm25_pending SELECT DISTINCT product_id,version FROM chunks')
    for event, references in (('INSERT', ('new',)), ('DELETE', ('old',)), ('UPDATE', ('old', 'new'))):
        queued = '\n'.join(
            f'INSERT OR IGNORE INTO rag_bm25_pending VALUES ({ref}.product_id,{ref}.version);' for ref in references)
        db.execute(f'CREATE TRIGGER rag_bm25_chunks_{event.lower()} AFTER {event} ON chunks BEGIN {queued} END')


def refresh(db):
    """Build once, then repair only scopes that were changed in this database."""
    if not dirty(db):
        return
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    if not schema_exists(db):
        create_schema(db)
    scopes = db.execute('SELECT product_id,version FROM rag_bm25_pending').fetchall()
    for product_id, version in scopes:
        scope = scope_token(product_id, version)
        db.execute('DELETE FROM rag_bm25 WHERE rag_bm25 MATCH ?', ('scope:' + scope,))
        chunks = db.execute('SELECT id,text FROM chunks WHERE product_id=? AND version=?', (product_id, version))
        db.executemany('INSERT INTO rag_bm25(scope,tokens,chunk_id) VALUES(?,?,?)',
                       ((scope, ' '.join(terms(text)), chunk_id) for chunk_id, text in chunks))
    db.execute('DELETE FROM rag_bm25_pending')


def candidates(db, product_id, version, text, alternatives, top_k):
    if not available(db):
        return None, 'fts5_unavailable'
    # Expand concise domain terms first. A fixed cap bounds the number of OR terms.
    keywords = list(islice(dict.fromkeys(terms(' '.join((*alternatives, text)))), 72))
    if not keywords:
        return None, 'no_index_terms'
    try:
        refresh(db)
        scope = scope_token(product_id, version)
        tokens = ' OR '.join(f'"{keyword}"' for keyword in keywords)
        match = f'scope:{scope} AND tokens:({tokens})'
        hits = db.execute('SELECT chunk_id FROM rag_bm25 WHERE rag_bm25 MATCH ? ORDER BY rank LIMIT ?',
                          (match, max(CANDIDATE_LIMIT, top_k))).fetchall()
    except sqlite3.OperationalError as error:
        if 'no such module: fts5' not in str(error).lower():
            raise
        return None, 'fts5_unavailable'
    return [row[0] for row in hits], None


def postgres_candidates(db, product_id, version, text, alternatives, top_k):
    """Score the current product/version with BM25 and return IDs for reranking."""
    from collections import Counter
    from math import log1p

    keywords = set(islice(dict.fromkeys(terms(' '.join((*alternatives, text)))), 72))
    if not keywords:
        return None, 'no_index_terms'
    rows = db.execute('SELECT id,text FROM chunks WHERE product_id=? AND version=? ORDER BY rowid',
                      (product_id, version)).fetchall()
    documents = [(row['id'], Counter(terms(row['text']))) for row in rows]
    lengths = [sum(tokens.values()) for _, tokens in documents]
    average_length = sum(lengths) / max(1, len(lengths)) or 1
    frequencies = Counter(term for _, tokens in documents for term in keywords & tokens.keys())
    weights = {term: log1p((len(documents) - count + .5) / (count + .5))
               for term, count in frequencies.items()}
    scored = []
    for length, (chunk_id, tokens) in zip(lengths, documents):
        norm = 1.2 * (.25 + .75 * length / average_length)
        score = sum(weights[term] * tokens[term] * 2.2 / (tokens[term] + norm)
                    for term in keywords & tokens.keys())
        if score:
            scored.append((chunk_id, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return [chunk_id for chunk_id, _ in scored[:max(CANDIDATE_LIMIT, top_k)]], None
