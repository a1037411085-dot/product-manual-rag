"""Opt-in integration coverage for real PostgreSQL and Chroma services."""
from contextlib import closing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from app import chroma_index, core, postgres, semantic, snapshots
from migrate_sqlite import migrate


@unittest.skipUnless(os.getenv('RAG_INTEGRATION_DATABASE_URL') and chroma_index.configured(),
                     'set RAG_INTEGRATION_DATABASE_URL and Chroma connection variables')
class PostgresChromaTests(unittest.TestCase):
    def test_cutover_preserves_history_and_synchronizes_retrieval(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'original.sqlite3'
            with patch.dict(os.environ, {'RAG_LEGACY_TEST': '1', 'RAG_RETRIEVER': 'hash',
                                         'RAG_ANSWER_MODE': 'extractive'}), patch.object(core, 'DB_PATH', source):
                os.environ.pop('RAG_DATABASE_URL', None)
                core.init_db()
                core.add_product('box', '云盒')
                core.add_document('box', '1.0', '说明', '手册', 'guide.md',
                                  '童锁开启后会禁用按键。'.encode())
                original = core.ask('童锁开启后有什么影响？', product_id='box', version='1.0')
            original_bytes = source.read_bytes()
            schema = 'rag_check_' + uuid.uuid4().hex
            with patch.dict(os.environ, {
                'RAG_DATABASE_URL': os.environ['RAG_INTEGRATION_DATABASE_URL'],
                'RAG_POSTGRES_SCHEMA': schema, 'RAG_RETRIEVER': 'chroma',
                'RAG_ANSWER_MODE': 'extractive', 'RAG_LLM_CONFIG': str(source.with_name('empty.json')),
                'RAG_LEGACY_DB': str(source),
            }), patch.object(semantic, 'embed', side_effect=lambda texts: semantic.normalize(
                [[0, 1] if '重量' in text else [1, 0] for text in texts])):
                try:
                    with closing(postgres.connect()) as db:
                        db.execute(f'CREATE SCHEMA "{schema}"')
                    core.init_db()
                    from fastapi.testclient import TestClient
                    from app import main

                    with TestClient(main.app) as api, patch.dict(main.migration_job, {'status': 'idle'}, clear=True):
                        self.assertIn('一键迁移旧库', api.get('/').text)
                        before = api.get('/api/rag/migration').json()
                        self.assertTrue(before['available'])
                        self.assertEqual(before['source'], source.name)
                        self.assertEqual(api.post('/api/rag/migration').status_code, 415)
                        started = api.post('/api/rag/migration', json={})
                        self.assertEqual(started.status_code, 202, started.text)
                        self.assertEqual(started.json()['status'], 'running')
                        completed = api.get('/api/rag/migration').json()
                        self.assertEqual(completed['status'], 'completed', completed)
                        first = completed['result']
                    self.assertEqual(first['postgres_tables']['messages'], 2)
                    self.assertEqual(first['chroma']['indexed'], 1)
                    self.assertEqual(first['chroma']['embedded'], 1)
                    again = migrate(source)
                    self.assertTrue(again['resumed'])
                    self.assertEqual(again['chroma']['embedded'], 0)
                    self.assertEqual(again['chroma']['upserted'], 0)
                    self.assertEqual(source.read_bytes(), original_bytes)
                    result = core.ask('童锁开启后有什么影响？', original['session_id'])
                    self.assertEqual(result['status'], 'answered')
                    self.assertGreater(result['message_id'], original['message_id'])
                    self.assertEqual(result['sources'][0]['source'], 'guide.md')

                    new = core.add_document('box', '2.0', '说明', '手册', 'new.md',
                                            '产品重量：45 kg。'.encode(), include_training=True)
                    rows = core.retrieve('产品重量是多少？', 'box', '2.0')
                    self.assertEqual([row['source'] for row in rows], ['new.md'])
                    with TestClient(main.app) as api:
                        path = '/api/documents/' + new['document_id'] + '/faqs'
                        all_faqs = api.get(path)
                        self.assertEqual(all_faqs.status_code, 200, all_faqs.text)
                        self.assertGreater(all_faqs.json()['total'], 0)
                        filtered = api.get(path, params={'q': '45 KG', 'limit': 1})
                        self.assertEqual(filtered.status_code, 200, filtered.text)
                        item = filtered.json()['items'][0]
                        self.assertIn('45 kg', item['answer'])
                        self.assertEqual(item['source_line'], 1)
                        literal = api.get(path, params={'q': '%'})
                        self.assertEqual(literal.json()['total'], 0)
                    with closing(core.connect()) as db, db:
                        db.execute('DELETE FROM documents WHERE id=?', (new['document_id'],))
                    self.assertEqual(core.retrieve('产品重量是多少？', 'box', '2.0'), [])
                    self.assertEqual(chroma_index.collection().count(), 1)

                    with TestClient(main.app) as api:
                        for path in ('/api/rag/status', '/api/products', '/api/documents', '/api/stats'):
                            response = api.get(path)
                            self.assertEqual(response.status_code, 200, response.text)
                        response = api.post('/api/test-sets', json={'name': 'PostgreSQL 评测'})
                        self.assertEqual(response.status_code, 201, response.text)
                        path = '/api/test-sets/' + response.json()['id']
                        case = {'question': '童锁开启后有什么影响？', 'product_id': 'box', 'version': '1.0',
                                'expected_terms': ['禁用按键'], 'expected_source': 'guide.md'}
                        self.assertEqual(api.post(path + '/cases', json=case).status_code, 201)
                        started = api.post(path + '/runs', json={'variants': [
                            {'name': '字符'}, {'name': 'BM25', 'retriever': 'bm25'}]})
                        self.assertEqual(started.status_code, 202, started.text)
                        report = api.get('/api/test-runs/' + started.json()['id']).json()
                        self.assertEqual(report['status'], 'completed', report)

                    with snapshots.schema_snapshot() as isolated:
                        with closing(postgres.connect(isolated)) as db:
                            self.assertEqual(db.execute('SELECT count(*) FROM chunks').fetchone()[0], 1)
                            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
                finally:
                    try:
                        chroma_index.delete_collection()
                    except Exception:
                        pass
                    finally:
                        with closing(postgres.connect()) as db:
                            db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


if __name__ == '__main__':
    unittest.main()
