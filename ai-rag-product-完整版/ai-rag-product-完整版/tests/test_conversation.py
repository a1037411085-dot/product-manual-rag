import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app import core


class ConversationalAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_db = core.DB_PATH
        core.DB_PATH = Path(self.temp.name) / 'conversation.sqlite3'
        core.init_db()
        core.add_product('cloud_box', '云盒')
        core.add_document('cloud_box', '2.0', '说明', '帮助', 'help.md',
                          ('# 常见问题\nQ: 云盒如何重置密码？\n'
                           'A: 在登录页点击找回密码，然后使用注册邮箱重置。\n'
                           '云盒 2.0 的附件上传限制为单个文件 25 MB。').encode())

    def tearDown(self):
        core.DB_PATH = self.previous_db
        self.temp.cleanup()

    def test_display_answer_is_warmer_but_keeps_factual_answer_and_source(self):
        result = core.ask('云盒如何重置密码？', product_id='cloud_box', version='2.0')
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['answer'], '在登录页点击找回密码,然后使用注册邮箱重置。')
        self.assertIn(result['answer'], result['display_answer'])
        self.assertNotEqual(result['answer'], result['display_answer'])
        self.assertEqual(result['sources'][0]['source'], 'help.md')
        self.assertTrue(any('上传' in q for q in result['suggested_questions']))
        self.assertFalse(any('重置密码' in q for q in result['suggested_questions']))

    def test_missing_fact_explains_scope_without_inventing_answer(self):
        result = core.ask('云盒支持语音转写吗？', product_id='cloud_box', version='2.0')
        self.assertEqual(result['status'], 'no_answer')
        self.assertIn('云盒 2.0', result['display_answer'])
        self.assertIn('没在', result['display_answer'])
        self.assertEqual(result['sources'], [])
        self.assertEqual(result['suggested_questions'], [])

    def test_legacy_messages_are_preserved_and_new_turns_record_version(self):
        with core.connect() as db:
            db.execute('ALTER TABLE messages DROP COLUMN version')
            db.execute("INSERT INTO sessions VALUES('legacy', 'cloud_box', '2.0', '2026-11-24')")
            db.execute('''INSERT INTO messages(session_id,role,text,created_at,product_id)
                          VALUES('legacy','user','旧消息','2026-11-24','cloud_box')''')
        core.init_db()
        core.init_db()
        result = core.ask('云盒如何重置密码？', 'legacy')
        self.assertEqual(result['status'], 'answered')
        with core.connect() as db:
            rows = db.execute("SELECT text,version FROM messages WHERE session_id='legacy' ORDER BY id")
            self.assertEqual([tuple(row) for row in rows], [
                ('旧消息', None), ('云盒如何重置密码？', '2.0'), (result['answer'], '2.0'),
            ])

    def test_grounded_partial_answer_and_full_refusal_have_distinct_statuses(self):
        partial = ('在登录页点击找回密码，通过注册邮箱重置。[1]\n'
                   '对于链接有效期，现有产品资料中暂未找到相关说明。')
        refusal = '现有产品资料中暂未找到相关说明。资料未写明链接有效期。'
        with patch.dict('os.environ', {'RAG_ANSWER_MODE': 'llm'}):
            for answer, status in ((partial, 'answered'), (refusal, 'no_answer')):
                with self.subTest(status=status), patch.object(core, 'llm_answer', return_value=answer):
                    result = core.ask('云盒如何重置密码？', product_id='cloud_box', version='2.0')
                    self.assertEqual(result['status'], status)
                    self.assertEqual(result['answer'], answer)
                    self.assertIn(answer, result['display_answer'])
                    self.assertEqual(bool(result['sources']), status == 'answered')

    def test_overlapping_followup_is_regenerated_with_latest_committed_history(self):
        initial = core.ask('云盒如何重置密码？', product_id='cloud_box', version='2.0')
        sid = initial['session_id']
        started, release = Event(), Event()
        histories = []
        new_answer = '请在登录页点击找回密码，再使用注册邮箱。[1]'

        def generate(question, sources, *, history=()):
            if history:
                histories.append(history)
                if len(histories) == 1:
                    started.set()
                    if not release.wait(10):
                        raise TimeoutError('test did not release generation')
                return '在登录页点击找回密码。[1]'
            return new_answer

        with patch.dict('os.environ', {'RAG_ANSWER_MODE': 'llm'}), \
                patch.object(core, 'llm_answer', side_effect=generate), ThreadPoolExecutor() as pool:
            followup = pool.submit(core.ask, '那云盒如何重置密码呢？', sid)
            try:
                self.assertTrue(started.wait(5))
                concurrent = pool.submit(core.ask, '云盒如何重置密码？', sid).result(timeout=3)
                self.assertEqual(concurrent['answer'], new_answer)
            finally:
                release.set()
            result = followup.result(timeout=3)

        self.assertEqual(result['status'], 'answered')
        self.assertEqual(len(histories), 2)
        self.assertEqual(histories[1][-1]['content'], new_answer)
        with core.connect() as db:
            rows = db.execute('SELECT id,role FROM messages WHERE session_id=? ORDER BY id', (sid,)).fetchall()
        self.assertEqual([row['role'] for row in rows], ['user', 'assistant'] * 3)
        self.assertEqual([row['id'] for row in rows[1::2]],
                         [initial['message_id'], concurrent['message_id'], result['message_id']])


if __name__ == '__main__':
    unittest.main()
