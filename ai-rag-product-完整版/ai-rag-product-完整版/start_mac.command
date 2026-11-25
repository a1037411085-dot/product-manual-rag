#!/bin/bash
set -euo pipefail

cd -- "$(dirname -- "$0")"

fail() {
    printf '\n启动失败：%s\n' "$1" >&2
    exit 1
}

supported_python() {
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 9))' >/dev/null 2>&1
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
        fail '请先安装Python 3.9+（例如用 Homebrew 执行 brew install python@3.11），再运行此脚本。'
    fi
    printf '正在准备项目虚拟环境…\n'
    "$python_cmd" -m venv .venv || fail '无法准备 .venv，请检查 Python 安装及项目目录的写入权限。'
fi

if ! "$venv_python" -m pip --version >/dev/null 2>&1; then
    "$venv_python" -m ensurepip --upgrade || fail '无法初始化 pip。'
fi

dependencies_ready() {
    "$venv_python" -c 'import chromadb, dotenv, fastapi, multipart, openpyxl, psycopg, pypdf, uvicorn; from pydantic import BaseModel; from app import core; assert hasattr(BaseModel, "model_dump")' >/dev/null 2>&1 &&
        "$venv_python" -m pip check >/dev/null 2>&1
}

if ! dependencies_ready; then
    printf '正在安装项目依赖，首次安装需要联网…\n'
    "$venv_python" -m pip install --disable-pip-version-check -r requirements-chroma.txt || fail '依赖安装失败，请检查网络和上方 pip 输出。'
    dependencies_ready || fail '依赖校验失败，请检查 .venv 中的 Python 和已安装依赖。'
fi

exec "$venv_python" run_server.py "$@"
