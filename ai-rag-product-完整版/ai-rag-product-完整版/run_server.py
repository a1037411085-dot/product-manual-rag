"""Start the demo on an available local port."""

import argparse
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def available_port(start=8000, stop=8010):
    for port in range(start, stop + 1):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError as exc:
                if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                    continue
                raise
            return port
    raise RuntimeError(f'端口 {start}–{stop} 均被占用，请关闭旧服务后重试。')


def start_compose(service, label):
    print(f'正在启动本地 {label}…', flush=True)
    try:
        subprocess.run(['docker', 'compose', 'up', '-d', '--wait', '--wait-timeout', '60', service],
                       cwd=ROOT, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f'本地 {label} 启动失败。请确认 Docker Desktop 已运行，并检查上方 Compose 输出。') from error


def check_chroma(local):
    print('正在检查 Chroma 连接…', flush=True)
    deadline = time.monotonic() + 20
    command = [sys.executable, '-c',
               'from app.chroma_index import chroma_client; chroma_client().count_collections()']
    while True:
        try:
            subprocess.run(command, cwd=ROOT, check=True, timeout=4 if local else 20, capture_output=True)
            break
        except (OSError, subprocess.SubprocessError) as error:
            if not local or time.monotonic() >= deadline:
                detail = '请检查本地容器和 CHROMA_PORT。' if local else '请检查服务地址、网络和 Chroma Cloud 凭据。'
                raise RuntimeError('Chroma 连接失败。' + detail) from error
            time.sleep(0.5)
    print('Chroma 已就绪。', flush=True)


def prepare_chroma():
    from app import chroma_index
    from dotenv import set_key

    host = os.getenv('CHROMA_HOST')
    managed = os.getenv('CHROMA_MANAGED') == '1' and host in (None, '', '127.0.0.1', 'localhost')
    if not managed and chroma_index.configured():
        check_chroma(local=False)
        return
    if not managed and any(os.getenv(key) for key in ('CHROMA_API_KEY', 'CHROMA_TENANT', 'CHROMA_DATABASE')):
        raise RuntimeError('检测到未填完整的 Chroma Cloud 配置。请补全配置，或清空这三项以启动本地 Chroma。')

    settings = {'CHROMA_MANAGED': '1', 'CHROMA_HOST': '127.0.0.1',
                'CHROMA_PORT': os.getenv('CHROMA_PORT') or '8001', 'CHROMA_SSL': '0'}
    os.environ.update(settings)
    for key in ('CHROMA_API_KEY', 'CHROMA_TENANT', 'CHROMA_DATABASE'):
        os.environ.pop(key, None)
    for key in ('NO_PROXY', 'no_proxy'):
        current = os.getenv(key)
        os.environ[key] = ','.join(filter(None, (current, '127.0.0.1', 'localhost')))
    start_compose('chroma', 'Chroma')
    check_chroma(local=True)
    if not managed:
        try:
            env_file = ROOT / '.env'
            with tempfile.TemporaryDirectory(prefix='.chroma-env-', dir=ROOT) as directory:
                staged = Path(directory) / '.env'
                if env_file.exists():
                    staged.write_bytes(env_file.read_bytes())
                for key, value in settings.items():
                    set_key(staged, key, value, quote_mode='auto')
                staged.replace(env_file)
        except OSError as error:
            raise RuntimeError('Chroma 已启动，但无法写入 .env。请检查项目目录是否可写。') from error
        print('已将本地 Chroma 连接配置写入 .env。', flush=True)


def prepare_services(use_chroma):
    import psycopg

    if use_chroma:
        prepare_chroma()

    if not os.getenv('RAG_DATABASE_URL'):
        password = os.getenv('POSTGRES_PASSWORD')
        if not password:
            raise RuntimeError('请在 .env 中设置本地 POSTGRES_PASSWORD，或填写已有实例的 RAG_DATABASE_URL。')
        start_compose('postgres', 'PostgreSQL')
        os.environ['RAG_DATABASE_URL'] = psycopg.conninfo.make_conninfo(
            host='127.0.0.1', port=os.getenv('POSTGRES_PORT') or '5432',
            dbname=os.getenv('POSTGRES_DB') or 'rag', user=os.getenv('POSTGRES_USER') or 'rag', password=password)

    try:
        with psycopg.connect(os.environ['RAG_DATABASE_URL'], connect_timeout=5) as db:
            db.execute('SELECT 1').fetchone()
    except (psycopg.Error, ValueError) as error:
        raise RuntimeError('PostgreSQL 连接失败。请检查地址、端口和数据库账号。') from error
    print('PostgreSQL 已就绪。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--migrate-sqlite', type=Path, help='校验并导入旧 SQLite 库，然后启动新版服务')
    mode.add_argument('--setup-only', action='store_true', help='准备数据库并验证服务连接')
    mode.add_argument('--setup-chroma', action='store_true', help='准备 Chroma 并在首次本地启动后保存连接配置')
    mode.add_argument('--setup-embedding', action='store_true', help='下载并检查本地 Embedding 模型')
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(ROOT / '.env', interpolate=False)
    retriever = os.environ.setdefault('RAG_RETRIEVER', 'hybrid')
    if args.setup_embedding:
        from app import semantic

        try:
            semantic.prepare_local_model()
        except Exception as error:
            parser.exit(1, f'本地 Embedding 模型准备失败（{type(error).__name__}）。请检查模型名称、网络和本地缓存后重试。\n')
        return
    try:
        if args.setup_chroma:
            prepare_chroma()
            return
        prepare_services(use_chroma=retriever in ('chroma', 'hybrid') or args.migrate_sqlite is not None)
    except RuntimeError as error:
        parser.exit(1, f'启动失败：{error}\n')

    from app import core, postgres, semantic

    if args.setup_only:
        core.init_db()
        print('环境检查和数据库初始化完成。', flush=True)
        return
    if args.migrate_sqlite:
        from migrate_sqlite import migrate
        print(json.dumps(migrate(args.migrate_sqlite, prepare_embeddings=True), ensure_ascii=False, indent=2), flush=True)
    import uvicorn

    port = available_port()
    if port != 8000:
        print(f'端口 8000 已被占用，旧页面可能仍在运行。请打开下面的新地址：', flush=True)
    print(f'\n产品知识问答演示：http://127.0.0.1:{port}/\n', flush=True)
    print(f'当前数据库：PostgreSQL / {postgres.schema_name()}\n', flush=True)
    model_setup = None
    if retriever in ('chroma', 'semantic', 'hybrid') and semantic.config()[0] == 'local':
        model_setup = subprocess.Popen([sys.executable, str(ROOT / 'run_server.py'), '--setup-embedding'], cwd=ROOT)
    try:
        uvicorn.run('app.main:app', host='127.0.0.1', port=port)
    finally:
        if model_setup is not None and model_setup.poll() is None:
            model_setup.terminate()
            try:
                model_setup.wait(timeout=5)
            except subprocess.TimeoutExpired:
                model_setup.kill()
                model_setup.wait()


if __name__ == '__main__':
    main()
