import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import core
from app.main import app
from app.testset_worker import contains


class ManagedTestSetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        db_scope = patch.object(core, 'DB_PATH', Path(self.temp.name) / 'managed.sqlite3')
        db_scope.start()
        self.addCleanup(db_scope.stop)
        core.init_db()
        core.add_product('test_box', '测试盒')
        core.add_document('test_box', '1.0', '说明', '参数', 'spec.txt', '额定容量：25 kg'.encode())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.set_id = self.client.post('/api/test-sets', json={'name': '评测集'}).json()['id']
        self.path = '/api/test-sets/' + self.set_id

    def test_csv_xlsx_validation_and_atomic_import(self):
        from openpyxl import Workbook

        bad = '问题,产品ID,版本,关键词\n额定容量是多少,test_box,1.0,25 kg\n空标注,test_box,1.0,\n'
        result = self.client.post(self.path + '/import', files={'file': ('invalid.csv', bad.encode())})
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIn('第 3 行', result.json()['detail'])
        self.assertEqual(self.client.get(self.path).json()['cases'], [])
        valid = bad.splitlines()[:2]
        result = self.client.post(self.path + '/import', files={'file': ('valid.csv', '\n'.join(valid).encode('gb18030'))})
        self.assertEqual(result.status_code, 201, result.text)
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(['question', 'product_id', 'version', 'expected_status', 'expected_terms'])
        worksheet.append(['额定容量是多少？', 'test_box', '1.0', 'answered', '额定容量｜25 kg'])
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        result = self.client.post(self.path + '/import', files={'file': ('test.xlsx', content.getvalue())})
        self.assertEqual(result.status_code, 201, result.text)
        cases = self.client.get(self.path).json()['cases']
        self.assertEqual(cases[1]['expected_terms'], ['额定容量', '25 kg'])
        self.assertEqual(result.json()['imported'], 1)

    def test_runs_comparisons_scoring_and_history_are_isolated(self):
        case = {'question': '额定容量是多少？', 'product_id': 'test_box', 'version': '1.0',
                'expected_terms': ['25 kg'], 'expected_source': 'spec.txt', 'expected_evidence': '25 kg'}
        result = self.client.post(self.path + '/cases', json=case)
        self.assertEqual(result.status_code, 201, result.text)
        case_id = result.json()['id']
        variants = [{'name': '基础', 'threshold': 0.01}, {'name': '高阈值', 'threshold': 1}]
        with patch.dict(os.environ, {'RAG_RETRIEVER': 'hash', 'RAG_LLM_DISABLED': '1'}):
            result = self.client.post(self.path + '/runs', json={'variants': variants})
        self.assertEqual(result.status_code, 202, result.text)
        first_id = result.json()['id']
        run_path = '/api/test-runs/' + first_id
        run = self.client.get(run_path).json()
        self.assertEqual(run['status'], 'completed', run)
        first, second = run['variants']
        self.assertEqual(first['metrics']['hit_at_k']['1'], 1)
        self.assertEqual(first['rows'][0]['score'], 100)
        self.assertIn('false_refusal', second['rows'][0]['reasons'])
        self.assertEqual(run['comparisons'][0]['regressions'][0]['case_id'], case_id)
        response = self.client.put(run_path + '/variants/0/cases/' + case_id + '/review',
                                   json={'score': 90, 'note': '证据充分'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(run_path + '/report?format=json').json()['variants'][0]['metrics']['human_average_score'], 90)
        self.assertIn('回归对比', self.client.get(run_path + '/report').text)

        case['expected_terms'] = ['99 kg']
        self.assertEqual(self.client.put(self.path + '/cases/' + case_id, json=case).status_code, 200)
        result = self.client.post(self.path + '/runs', json={'variants': [variants[0]], 'baseline_run_id': first_id})
        self.assertEqual(result.status_code, 202, result.text)
        next_run = self.client.get('/api/test-runs/' + result.json()['id']).json()
        comparison = next_run['comparisons'][0]
        self.assertTrue(comparison['same_corpus'])
        self.assertEqual(comparison['comparable'], 0)
        self.assertEqual(comparison['edited'][0]['case_id'], case_id)
        self.assertEqual(self.client.delete(self.path).status_code, 200)
        self.assertEqual(self.client.get(run_path).json()['status'], 'completed')
        with core.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        self.assertFalse(contains('额定容量 25 kg', '5 kg'))
        self.assertTrue(contains('额定容量：２５ kg', '25 KG'))

    def test_document_view_keeps_grouped_steps_and_source_lines(self):
        content = ('# 连接 Wi-Fi\n1. 打开设备设置。\n2. 选择无线网络。\n'
                   '提示：\n本产品仅支持 2.4 GHz 网络。\n'
                   '# 规格\n| 额定功率 | 45 W |\n'
                   'Q: 怎样确认连接成功？\nA:\n1. 查看指示灯。\n2. 检查 App 是否显示在线。').encode()
        args = {'data': {'product_id': 'test_box', 'version': '2.0'}, 'files': {'file': ('guide.md', content)}}
        preview_response = self.client.post('/api/documents/preview', **args)
        self.assertEqual(preview_response.status_code, 200, preview_response.text)
        preview = preview_response.json()
        faq = next(f for f in preview['faqs'] if f['kind'] == 'procedure')
        self.assertIn('2.4 GHz', faq['answer'])
        explicit = next(f for f in preview['faqs'] if f['kind'] == 'explicit')
        self.assertIn('显示在线', explicit['answer'])
        self.assertTrue(any(f['kind'] == 'parameter' and '45 W' in f['answer'] for f in preview['faqs']))
        saved = self.client.post('/api/documents', **args).json()
        path = '/api/documents/' + saved['document_id'] + '/faqs'
        response = self.client.get(path, params={'q': '45 W', 'limit': 1})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['document']['version'], '2.0')
        self.assertEqual(result['items'][0]['source_line'], 7)


if __name__ == '__main__':
    unittest.main()
