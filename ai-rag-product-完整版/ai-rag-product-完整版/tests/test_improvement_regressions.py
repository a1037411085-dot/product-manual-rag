import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import core
from app.testset_worker import answer_scores, compare_rows


class ImprovementRegressionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for context in (patch.object(core, 'DB_PATH', Path(temp.name) / 'test.sqlite3'),
                        patch.dict(os.environ, {'RAG_RETRIEVER': 'hash', 'RAG_ANSWER_MODE': 'extractive',
                                                'RAG_LLM_DISABLED': '1', 'RAG_MIN_SCORE': '0.16'})):
            context.start()
            self.addCleanup(context.stop)
        core.init_db()
        core.add_product('fridge', '试验冰箱')
        core.add_product('robot', '扫拖机器人')
        manual = ('型号：BCD-600AA1\n总容积：600L\n冷冻室容积：195L\n\n'
                  '型号：BCD-601AA1\n总容积：601L\n冷冻室容积：196L\n\n'
                  '# 故障码\n| 代码 | 问题 |\n| --- | --- |\n| E1 | 传感器断开 |')
        core.add_document('fridge', '1.0', '说明', '冰箱', 'fridge.md', manual.encode(), include_training=True)
        core.add_document('fridge', '2.0', '说明', '冰箱', 'fridge2.md',
                          '型号：BCD-601AA1\n总容积：607L\n'.encode(), include_training=True)
        core.add_document('robot', '1.0', '说明', '机器人', 'robot.md',
                          '额定功率：45 W\n产品净重：3.4 kg\n工作时清扫地面。'.encode())

    def test_model_spec_is_bound_to_exact_model_and_version(self):
        for version, capacity in [('1.0', '601L'), ('2.0', '607L')]:
            result = core.ask('BCD-601AA1 的总容积是多少？', product_id='fridge', version=version)
            self.assertIn(capacity, result['answer'])
            self.assertIn('BCD-601AA1', result['sources'][0]['text'])
        for question in ('BCD-602AA1 的总容积是多少？', 'BCD-600AA1 和 BCD-601AA1 总容积是多少？'):
            self.assertEqual(core.ask(question, product_id='fridge', version='1.0')['status'], 'no_answer')
        with core.connect() as db:
            rows = db.execute("SELECT text FROM chunks WHERE product_id='fridge' AND version='1.0'")
            self.assertTrue(any('| 代码 | 问题 |\n| E1 | 传感器断开 |' in row['text'] for row in rows))

    def test_followup_inherits_field_and_never_carries_old_version_into_model_history(self):
        first = core.ask('BCD-601AA1 的总容积是多少？', product_id='fridge', version='1.0')
        sid = first['session_id']
        result = core.ask('那冷冻室呢？', sid)
        self.assertIn('冷冻室容积', result['rewritten_question'])
        self.assertIn('196L', result['answer'])
        independent = core.ask('产品净重是多少？', sid, product_id='robot', version='1.0')
        self.assertEqual(independent['rewritten_question'], '产品净重是多少？')
        with patch.dict(os.environ, {'RAG_ANSWER_MODE': 'llm'}), patch.object(core, 'llm_answer', return_value='607L [1]') as call:
            switched = core.ask('那 BCD-601AA1 的总容积呢？', sid, product_id='fridge', version='2.0')
            self.assertIn('607L', switched['answer'])
            self.assertNotIn('history', call.call_args.kwargs)
            follow = core.ask('那总容积是多少？', sid)
            self.assertEqual(follow['status'], 'answered')
            turns = call.call_args.kwargs['history']
            self.assertTrue(any('607L' in turn['content'] for turn in turns))
            self.assertFalse(any('196L' in turn['content'] or '净重' in turn['content'] for turn in turns))

    def test_robot_work_never_matches_human_handoff_and_service_actions_do(self):
        def status(question):
            return core.ask(question, product_id='robot', version='1.0')['status']

        self.assertNotEqual(status('扫拖机器人工作声音有多大？'), 'handoff')
        self.assertEqual(status('我要申请上门维修'), 'handoff')
        self.assertEqual(status('请转人工客服'), 'handoff')
        self.assertEqual(status('额定功率是多少？'), 'answered')

    def test_unsupported_citation_is_not_returned_as_fact(self):
        with patch.dict(os.environ, {'RAG_ANSWER_MODE': 'llm'}), patch.object(core, 'llm_answer', return_value='45W [99]'):
            result = core.ask('额定功率是多少？', product_id='robot', version='1.0')
        self.assertEqual(result['status'], 'no_answer')
        self.assertEqual(result['sources'], [])

    def test_aligned_evidence_must_support_requested_qualifier(self):
        for question, text, accepted in (
            ('这台冰箱卖多少钱？', '冰箱外形尺寸：500 mm', False),
            ('这台冰箱卖多少钱？', '这台冰箱售价：800 元', True),
            ('这台冰箱的质保期是多久？', '整机清洁通常需要 30 分钟', False),
            ('这台冰箱的质保期是多久？', '整机质保期为一年。', True),
        ):
            with self.subTest(question=question, text=text):
                result = core.enough_evidence(question, [{'text': text, 'score': 0.9}], 0.35, 'semantic', 'fridge')
                self.assertEqual(result, accepted)

    def test_units_and_strict_scoring_remain_distinct_and_old_baselines_are_comparable(self):
        case = {'id': 'qty', 'question': '容积？', 'expected_status': 'answered', 'expected_terms': ['600L']}
        self.assertEqual(answer_scores(case, '容积为600升。', True), (0, 100, []))
        self.assertEqual(answer_scores(case, '总容积为1600升。', True)[1], 0)
        self.assertEqual(answer_scores(case, '总容积为600.5升。', True)[1], 0)
        self.assertEqual(answer_scores(case, '总容积为600.0升。', True)[1], 100)
        self.assertEqual(answer_scores(case, '总容积为-600升。', True)[1], 0)
        self.assertEqual(answer_scores(case, '数值为600L/min。', True)[1], 0)
        old = {'case': case, 'score': 0, 'status_ok': True, 'actual_answer': '600升', 'citation_ok': None, 'evidence_rank': 1}
        current = {**old, 'score': 100, 'strict_score': 0, 'passed': True}
        comparison = compare_rows([old], [current])
        self.assertEqual(comparison['comparison_scoring'], 'literal')
        self.assertEqual(comparison['improvements'], [])
        self.assertEqual(comparison['score_gains'], [])


if __name__ == '__main__':
    unittest.main()
