#!/bin/bash
set -euo pipefail

cd -- "$(dirname -- "$0")"
setup_only=false
case "${1:-}" in
    '') ;;
    --setup-only) setup_only=true ;;
    -h|--help) printf '用法：%s [--setup-only]\n' "$0"; exit 0 ;;
    *) printf '用法：%s [--setup-only]\n' "$0" >&2; exit 2 ;;
esac

fail() {
    printf '\n启动失败：%s\n' "$1" >&2
    exit 1
}

supported_python() {
    "$1" -c 'import sqlite3, sys; sys.exit(not (sys.version_info >= (3, 9) and sqlite3.sqlite_version_info >= (3, 35)))' >/dev/null 2>&1
}

venv_python=.venv/bin/python
if ! supported_python "$venv_python"; then
    python_cmd=''
    for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /opt/homebrew/opt/python@3.11/bin/python3.11 /usr/local/opt/python@3.11/bin/python3.11; do
        if supported_python "$candidate"; then
            python_cmd="$candidate"
            break
        fi
    done
    if [[ -z "$python_cmd" ]]; then
        fail '请先安装带 SQLite 3.35 或更高版本的 Python 3.9+（例如用 Homebrew 执行 brew install python@3.11），再运行此脚本。'
    fi
    printf '正在准备项目虚拟环境…\n'
    "$python_cmd" -m venv .venv || fail '无法准备 .venv，请检查 Python 安装及项目目录的写入权限。'
fi

if ! "$venv_python" -m pip --version >/dev/null 2>&1; then
    "$venv_python" -m ensurepip --upgrade || fail '无法初始化 pip。'
fi

dependencies_ready() {
    "$venv_python" -c 'import fastapi, multipart, openpyxl, pypdf, uvicorn; from pydantic import BaseModel; from app import core; assert hasattr(BaseModel, "model_dump")' >/dev/null 2>&1 &&
        "$venv_python" -m pip check >/dev/null 2>&1
}

if ! dependencies_ready; then
    printf '正在安装项目依赖，首次安装需要联网…\n'
    "$venv_python" -m pip install --disable-pip-version-check -r requirements.txt || fail '依赖安装失败，请检查网络和上方 pip 输出。'
    dependencies_ready || fail '依赖校验失败，请检查 .venv 中的 Python 和已安装依赖。'
fi

if [[ -z "${RAG_DB:-}" ]]; then
    export RAG_DB="$PWD/data/rag.sqlite3"
    if [[ ! -e "$RAG_DB" && -f data/sms.sqlite3 ]]; then
        printf '正在创建内置说明书数据库的本地副本…\n'
        "$venv_python" - "$RAG_DB" <<'PY' || fail '无法创建说明书数据库的本地副本。'
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

target = Path(sys.argv[1])
source = Path('data/sms.sqlite3').resolve().as_uri() + '?mode=ro'
with tempfile.TemporaryDirectory(dir=target.parent) as temp:
    snapshot = Path(temp) / target.name
    with closing(sqlite3.connect(source, uri=True)) as original, closing(sqlite3.connect(snapshot)) as local:
        original.backup(local)
    try:
        os.link(snapshot, target)
    except FileExistsError:
        pass  # A concurrent launch already created the local database.
PY
    fi
fi

export RAG_RETRIEVER="${RAG_RETRIEVER:-hash}"
export RAG_ANSWER_MODE="${RAG_ANSWER_MODE:-extractive}"
"$venv_python" seed.py --if-empty || fail '数据库初始化失败，请检查上方输出。'

if "$setup_only"; then
    printf '环境和数据库已就绪。\n'
    exit 0
fi

exec "$venv_python" run_server.py
