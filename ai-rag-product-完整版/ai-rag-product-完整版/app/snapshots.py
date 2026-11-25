"""Isolated PostgreSQL schemas for benchmark runs and evaluation variants."""
from contextlib import closing, contextmanager
import os
import uuid

from . import core, postgres


KNOWLEDGE_TABLES = ('products', 'documents', 'document_texts', 'chunks', 'faqs', 'faq_origins',
                    'semantic_vectors', 'settings')


@contextmanager
def schema_snapshot(*, copy_knowledge=True):
    source = postgres.schema_name()
    schema = 'rag_eval_' + uuid.uuid4().hex
    with closing(postgres.connect()) as db:
        db.execute(f'CREATE SCHEMA "{schema}"')
    try:
        core.init_db(schema)
        if copy_knowledge:
            with closing(postgres.connect(schema)) as db, db:
                db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                for table in KNOWLEDGE_TABLES:
                    db.execute(f'INSERT INTO "{schema}".{table} SELECT * FROM "{source}".{table}')
                for table in ('documents', 'chunks', 'faqs'):
                    db.execute(
                        f"""SELECT setval(pg_get_serial_sequence(?, 'storage_order'),
                                    coalesce(max(storage_order), 1), count(*) > 0) FROM "{schema}".{table}""",
                        (f'{schema}.{table}',))
                db.execute("INSERT INTO settings(key,value) VALUES('answer_prompt',?) ON CONFLICT DO NOTHING",
                           (core.default_answer_prompt(),))
        yield schema
    finally:
        with closing(postgres.connect()) as db:
            db.execute(f'DROP SCHEMA "{schema}" CASCADE')


def environment(schema):
    values = os.environ.copy()
    values.update(RAG_POSTGRES_SCHEMA=schema, RAG_LLM_CONFIG=os.path.join('/__rag_eval__', schema))
    return values
