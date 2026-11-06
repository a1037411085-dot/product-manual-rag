@echo off
chcp 65001 >nul
cd /d %~dp0
echo ==================================================
echo  产品知识库问答系统（完整版 · 含 5 份说明书）
echo ==================================================
if not exist .venv (
  echo [1/4] 创建虚拟环境...
  python -m venv .venv || goto :fail
)
echo [2/4] 安装基础依赖...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :fail
echo [3/4] 安装语义检索依赖（首次约 250 MB，需联网）...
.venv\Scripts\python.exe -m pip install -q -r requirements-rag.txt || goto :semantic_fail
goto :run
:semantic_fail
echo.
echo 语义依赖安装失败，回退到字符检索模式（功能正常，仅少语义检索）。
set RAG_RETRIEVER=hash
goto :run
:run
echo [4/4] 启动服务...
set RAG_DB=%~dp0data\sms.sqlite3
if "%RAG_RETRIEVER%"=="" set RAG_RETRIEVER=semantic
set HF_HOME=%~dp0.cache\huggingface
set HF_HUB_OFFLINE=1
set HF_ENDPOINT=https://hf-mirror.com
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe run_server.py
goto :eof
:fail
echo.
echo 启动失败：请确认已安装 Python 3.9+ 且网络可用。
pause
