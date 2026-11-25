import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import bm25, core
from app.main import app


class Bm25Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        database = patch.object(core, 'DB_PATH', Path(temp.name) / 'bm25.sqlite3')
        settings = patch.dict(os.environ, {'RAG_RETRIEVER': 'bm25', 'RAG_ANSWER_MODE': 'extractive',
                                            'RAG_MIN_SCORE': '0.16'})
        for context in (database, settings):
            context.start()
            self.addCleanup(context.stop)
        core.init_db()
        core.add_product('one', '冰箱甲')
        core.add_product('two', '冰箱乙')
        self.first = self.document('one', '1.0', 'current.txt', '童锁开启后会禁用按键。')
        self.document('one', '2.0', 'other-version.txt', '童锁使用远程解锁。')
        self.document('two', '1.0', 'other-product.txt', '童锁使用机械钥匙。')

    def document(self, product, version, filename, text):
        return core.add_document(product, version, '说明', '手册', filename, text.encode())['document_id']

    def require_fts(self):
        with core.connect() as db:
            if not bm25.available(db):
                self.skipTest('SQLite without FTS5')

    def test_chinese_short_terms_and_scope_filter_in_chat(self):
        self.require_fts()
        with TestClient(app) as client:
            self.assertFalse(client.get('/api/rag/status').json()['bm25']['built'])
            response = client.post('/api/ask', json={'question': '童锁开启后有什么影响？',
                                                     'product_id': 'one', 'version': '1.0'})
            self.assertEqual(response.status_code, 200, response.text)
            chat = response.json()
            self.assertEqual(client.get('/api/rag/status').json()['bm25']['pending_scopes'], 0)
        self.assertEqual(chat['retriever'], 'bm25')
        self.assertEqual(chat['status'], 'answered')
        self.assertIn('禁用按键', chat['answer'])
        details = {}
        result = core.retrieve('童锁', 'one', '1.0', details=details)
        self.assertEqual([row['source'] for row in result], ['current.txt'])
        self.assertEqual(details['retrieval_candidates'], 1)
        self.assertNotIn('retrieval_fallback', details)

    def test_index_refreshes_after_append_and_cascade_delete(self):
        self.require_fts()
        with TestClient(app) as client:
            response = client.post('/api/rag/reindex/bm25')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['chunks'], 3)
            self.document('one', '1.0', 'new.txt', '净重：45 公斤。')
            self.assertEqual(client.get('/api/rag/status').json()['bm25']['pending_scopes'], 1)
            self.assertEqual(client.delete('/api/documents/' + self.first).status_code, 200)
            response = client.post('/api/ask', json={'question': '净重是多少？',
                                                     'product_id': 'one', 'version': '1.0'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('45', response.json()['answer'])
            details = {}
            found = core.retrieve('净重', 'one', '1.0', details=details)
            self.assertEqual([row['source'] for row in found], ['new.txt'])
            self.assertNotIn('retrieval_fallback', details)
            with core.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM rag_bm25 WHERE rag_bm25 MATCH 'tokens:童锁'").fetchone()[0], 2)
            self.assertEqual(client.get('/api/rag/status').json()['bm25']['pending_scopes'], 0)

    def test_managed_runs_can_compare_bm25_to_the_character_baseline(self):
        self.require_fts()
        with TestClient(app) as client:
            set_id = client.post('/api/test-sets', json={'name': '检索对照'}).json()['id']
            path = '/api/test-sets/' + set_id
            payload = {'question': '童锁开启后有什么影响？', 'product_id': 'one', 'version': '1.0',
                       'expected_terms': ['禁用按键'], 'expected_source': 'current.txt'}
            self.assertEqual(client.post(path + '/cases', json=payload).status_code, 201)
            response = client.post(path + '/runs', json={'variants': [
                {'name': '字符'}, {'name': 'BM25', 'retriever': 'bm25'}]})
            self.assertEqual(response.status_code, 202, response.text)
            report = client.get('/api/test-runs/' + response.json()['id']).json()
            self.assertEqual(report['status'], 'completed', report)
            first, second = report['variants']
            self.assertEqual(first['metrics']['strict_passed'], second['metrics']['strict_passed'])
            self.assertEqual(second['rows'][0]['retrieval_candidates'], 1)

    def test_cold_direct_chat_commits_index_and_allows_other_writers_during_generation(self):
        self.require_fts()
        started, release = Event(), Event()

        def generate(question, sources):
            if '童锁' in question:
                started.set()
                if not release.wait(10):
                    raise TimeoutError('test did not release generation')
                return '童锁开启后会禁用按键。[1]'
            return '净重为 45 公斤。[1]'

        with patch.dict(os.environ, {'RAG_ANSWER_MODE': 'llm'}), \
                patch.object(core, 'llm_answer', side_effect=generate), ThreadPoolExecutor() as pool:
            first = pool.submit(core.ask, '童锁开启后有什么影响？', 'first', 'one', '1.0')
            try:
                self.assertTrue(started.wait(5))
                with core.connect() as db:
                    self.assertTrue(bm25.status(db)['built'])
                    self.assertIsNone(db.execute("SELECT id FROM sessions WHERE id='first'").fetchone())
                added = pool.submit(self.document, 'one', '1.0', 'new.txt', '净重：45 公斤。')
                added.result(timeout=3)
                second = pool.submit(core.ask, '净重是多少？', 'second', 'one', '1.0').result(timeout=3)
                self.assertEqual(second['status'], 'answered')
                self.assertEqual(second['sources'][0]['source'], 'new.txt')
            finally:
                release.set()
            self.assertEqual(first.result(timeout=3)['status'], 'answered')

    def test_missing_fts_uses_character_ranking(self):
        details = {}
        with patch.object(bm25, 'available', return_value=False):
            hits = core.retrieve('童锁', 'one', '1.0', details=details)
        self.assertEqual(hits[0]['source'], 'current.txt')
        self.assertEqual(details['retrieval_fallback'], 'fts5_unavailable')


if __name__ == '__main__':
    unittest.main()
