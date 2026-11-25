import errno
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from run_server import available_port, prepare_chroma, prepare_services


class LauncherTests(unittest.TestCase):
    def test_launchers_forward_arguments_to_shared_startup(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / '启动.bat').read_text()
        self.assertIn('pip install -r requirements-chroma.txt', script)
        self.assertIn('run_server.py %*', script)
        self.assertIn('run_server.py "$@"', (root / 'start_mac.command').read_text())

    def test_local_startup_runs_compose_and_checks_both_services(self):
        from psycopg.conninfo import conninfo_to_dict

        env = {'POSTGRES_PASSWORD': 'test password @', 'POSTGRES_PORT': '55432', 'CHROMA_HOST': '127.0.0.1'}
        with patch.dict(os.environ, env, clear=True), patch('psycopg.connect') as connect, \
                patch('run_server.subprocess.run') as run:
            prepare_services(use_chroma=True)
        self.assertEqual(run.call_args_list[1].args[0],
                         ['docker', 'compose', 'up', '-d', '--wait', '--wait-timeout', '60', 'postgres'])
        self.assertIn('count_collections()', run.call_args_list[0].args[0][-1])
        dsn = conninfo_to_dict(connect.call_args.args[0])
        self.assertEqual(dsn['port'], '55432')
        self.assertEqual(dsn['password'], env['POSTGRES_PASSWORD'])

    def test_existing_postgres_skips_compose_and_chroma_failure_stops_startup(self):
        env = {'RAG_DATABASE_URL': 'postgresql://db.example/rag', 'CHROMA_HOST': 'chroma.example'}
        with patch.dict(os.environ, env, clear=True), patch('psycopg.connect') as connect, \
                patch('run_server.subprocess.run', side_effect=subprocess.TimeoutExpired('probe', 20)) as run:
            with self.assertRaisesRegex(RuntimeError, 'Chroma 连接失败'):
                prepare_services(use_chroma=True)
        connect.assert_not_called()
        run.assert_called_once()
        self.assertIn('count_collections()', run.call_args.args[0][-1])

    def test_missing_chroma_starts_local_service_and_saves_only_its_settings(self):
        from dotenv import dotenv_values

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            env_file = root / '.env'
            env_file.write_text('# Keep this setting\nRAG_RETRIEVER=chroma\nCHROMA_PORT=\n')
            with patch('run_server.ROOT', root), patch('run_server.subprocess.run') as run:
                prepare_chroma()
                self.assertEqual(run.call_args_list[0].args[0][-1], 'chroma')
                self.assertIn('count_collections()', run.call_args_list[1].args[0][-1])
                first_contents = env_file.read_text()
                self.assertIn('# Keep this setting', first_contents)
                self.assertEqual(dict(dotenv_values(env_file)), {
                    'RAG_RETRIEVER': 'chroma', 'CHROMA_MANAGED': '1', 'CHROMA_HOST': '127.0.0.1',
                    'CHROMA_PORT': '8001', 'CHROMA_SSL': '0',
                })
                prepare_chroma()
                self.assertEqual(run.call_args_list[2].args[0][-1], 'chroma')
                self.assertEqual(env_file.read_text(), first_contents)

    def test_chroma_not_saved_when_local_probe_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch('run_server.ROOT', Path(directory)), patch('run_server.start_compose'), \
                patch('run_server.check_chroma', side_effect=RuntimeError('Chroma 连接失败')):
            with self.assertRaisesRegex(RuntimeError, 'Chroma 连接失败'):
                prepare_chroma()
            self.assertFalse((Path(directory) / '.env').exists())

    def test_uses_next_port_when_default_is_occupied(self):
        attempts = []

        class Probe:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def bind(self, address):
                attempts.append(address[1])
                if address[1] == 8000:
                    raise OSError(errno.EADDRINUSE, 'busy')

        with patch('run_server.socket.socket', return_value=Probe()):
            self.assertEqual(available_port(), 8001)
        self.assertEqual(attempts, [8000, 8001])


if __name__ == '__main__':
    unittest.main()
