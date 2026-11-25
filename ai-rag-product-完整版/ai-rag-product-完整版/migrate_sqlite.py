"""Copy a legacy SQLite database into PostgreSQL and synchronize Chroma."""
import argparse
from contextlib import closing
import hashlib
import json
import pickle
import sqlite3
from pathlib import Path

from app import chroma_index, core, postgres, semantic

TABLES = ('products', 'documents', 'document_texts', 'chunks', 'faqs', 'faq_origins',
          'sessions', 'messages', 'handoffs', 'feedback', 'unanswered', 'settings',
          'training_runs', 'test_sets', 'test_cases', 'test_runs', 'test_reviews',
          'semantic_vectors')
SEQUENCES = ('messages', 'feedback', 'unanswered', 'training_runs')


class MigrationError(ValueError):
    """A migration error with a message safe to display to the operator."""


def source_rows(db, table, columns):
    quoted = ','.join('"' + name + '"' for name in columns)
    return db.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid')


def import_relational(source):
    core.init_db()
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as old, \
            closing(core.connect()) as new, new:
        old.execute('BEGIN')
        new.lock('sqlite-cutover')
        old_tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'products', 'documents', 'chunks'}.issubset(old_tables):
            raise MigrationError('源文件不是受支持的产品问答 SQLite 数据库。')
        new.execute('''CREATE TABLE IF NOT EXISTS legacy_import(
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), digest TEXT NOT NULL,
            counts TEXT NOT NULL)''')
        copied = {}
        digest = hashlib.sha256()
        source_columns = {}
        for table in TABLES:
            if table not in old_tables:
                continue
            valid = {row[0] for row in new.execute('''SELECT column_name FROM information_schema.columns
                                                       WHERE table_schema=current_schema() AND table_name=?''',
                                                   (table,))}
            columns = [row[1] for row in old.execute(f'PRAGMA table_info("{table}")') if row[1] in valid]
            source_columns[table] = columns
            digest.update(pickle.dumps((table, columns), protocol=4))
            count = 0
            for row in source_rows(old, table, columns):
                digest.update(pickle.dumps(row, protocol=4))
                count += 1
            copied[table] = count
        fingerprint = digest.hexdigest()
        previous = new.execute('SELECT digest,counts FROM legacy_import WHERE singleton=1').fetchone()
        if previous:
            if previous['digest'] != fingerprint:
                raise MigrationError('此 PostgreSQL schema 已导入另一份快照。请为本次迁移指定新的 schema。')
            return json.loads(previous['counts']), True
        # Freeze application writes between the emptiness check and the copy.
        new.execute('LOCK TABLE ' + ', '.join(TABLES) + ' IN SHARE ROW EXCLUSIVE MODE')
        for table in TABLES:
            if new.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                raise MigrationError(f'目标 schema 的 {table} 表已有数据。请使用空 schema。')
        for table, columns in source_columns.items():
            fields = ','.join('"' + name + '"' for name in columns)
            placeholders = ','.join('?' for _ in columns)
            query = f'INSERT INTO "{table}" ({fields}) VALUES ({placeholders})'
            rows = source_rows(old, table, columns)
            while True:
                batch = rows.fetchmany(128)
                if not batch:
                    break
                new.executemany(query, batch)
            actual = new.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            if actual != copied[table]:
                raise RuntimeError(f'{table} 行数校验失败。')
        if 'messages' in source_columns and 'product_id' not in source_columns['messages']:
            new.execute("""UPDATE messages m SET product_id=s.product_id FROM sessions s
                           WHERE s.id=m.session_id AND m.role='user'""")
        if 'unanswered' in source_columns and 'version' not in source_columns['unanswered']:
            new.execute('UPDATE unanswered u SET version=s.version FROM sessions s WHERE s.id=u.session_id')
        new.execute("""UPDATE test_runs SET status='failed',error='旧库迁移前的运行已结束，请重新执行'
                       WHERE status IN ('queued','running')""")
        for table in SEQUENCES:
            new.execute(f'''SELECT setval(pg_get_serial_sequence(?, 'id'),
                            coalesce(max(id), 1), count(*) > 0) FROM "{table}"''', (table,))
        new.execute('INSERT INTO legacy_import VALUES(1,?,?)', (fingerprint, json.dumps(copied)))
    return copied, False


def migrate(source, *, progress=None, prepare_embeddings=False):
    if not postgres.enabled() or not chroma_index.configured():
        raise MigrationError('先配置 RAG_DATABASE_URL 和 Chroma 连接，再执行迁移。')
    if not source.is_file():
        raise MigrationError('找不到迁移源文件。请检查 RAG_LEGACY_DB 或项目中的 data/rag.sqlite3。')
    remote = chroma_index.collection()
    schema = postgres.schema_name()
    if progress:
        progress('正在导入 PostgreSQL 并核对数据行数…')
    with closing(core.connect()) as db:
        db.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    copied, resumed = import_relational(source)
    if progress:
        progress('正在同步并核对 Chroma 向量…')
    try:
        indexed = chroma_index.reindex(core.connect)
    except semantic.EmbeddingNotReady:
        if not prepare_embeddings:
            raise
        semantic.prepare_local_model()
        indexed = chroma_index.reindex(core.connect)
    if indexed['chunks'] != indexed['indexed']:
        raise MigrationError('Chroma 向量数未通过核对。可以使用同一份旧库重试。')
    return {'postgres_tables': copied, 'resumed': resumed, 'chroma': indexed,
            'schema': schema, 'collection': remote.name}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, nargs='?', default=Path('data/rag.sqlite3'))
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, prepare_embeddings=True), ensure_ascii=False, indent=2))
