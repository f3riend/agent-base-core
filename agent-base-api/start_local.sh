#!/bin/bash
# Lokal geliştirme için 4 process'i tek komutla başlatır:
#   - uvicorn (FastAPI, port 8000)
#   - listener.py (timeline event polling)
#   - workflow_worker.py (scheduled_entries fire + wait resume)
#   - task_executor.py (background task runner)
#
# Yanlış .venv'den (örn. /home/bypasa10/Desktop/agent-base/...) eski
# uvicorn'lar varsa onları temizler. start_local.sh'nin bulunduğu dizine
# cd eder, sonra mevcut dizindeki `.venv/bin/python -m uvicorn` ile başlatır.

set -u

cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "[start] HATA: $VENV_PY yok. .venv aktif değil mi?"
  exit 1
fi

echo "[start] eski process'leri kapatıyor..."
pkill -f "python.*uvicorn"        2>/dev/null
pkill -f "uv run uvicorn"         2>/dev/null
pkill -f "python.*listener.py"    2>/dev/null
pkill -f "python.*workflow_worker" 2>/dev/null
pkill -f "python.*task_executor"  2>/dev/null
sleep 2

echo "[start] uvicorn (port 8000)..."
nohup "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/uvicorn.log 2>&1 &
UVICORN_PID=$!

echo "[start] listener.py..."
nohup "$VENV_PY" -u listener.py > /tmp/listener.log 2>&1 &
LISTENER_PID=$!

echo "[start] workflow_worker.py..."
nohup "$VENV_PY" -u workflow_worker.py > /tmp/workflow_worker.log 2>&1 &
WORKFLOW_PID=$!

echo "[start] task_executor.py..."
nohup "$VENV_PY" -u task_executor.py > /tmp/task_executor.log 2>&1 &
TASK_PID=$!

sleep 7

echo ""
echo "=== Process durumu ==="
pgrep -af "uvicorn|listener|workflow_worker|task_executor" \
  | grep -v "bash\|grep" \
  | head -10

echo ""
echo "=== Health ==="
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health)
if [ "$HEALTH" = "200" ]; then
  echo "uvicorn   OK  (200)"
else
  echo "uvicorn   HATA (HTTP $HEALTH) — /tmp/uvicorn.log incele"
fi

for proc in listener workflow_worker task_executor; do
  if pgrep -f "python.*${proc}" > /dev/null; then
    echo "$proc OK"
  else
    echo "$proc HATA — /tmp/${proc}.log incele"
  fi
done

echo ""
echo "Logs: /tmp/uvicorn.log /tmp/listener.log /tmp/workflow_worker.log /tmp/task_executor.log"
echo "Durdurmak için: pkill -f 'python.*(uvicorn|listener|workflow_worker|task_executor)'"
