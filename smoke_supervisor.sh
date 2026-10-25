#!/usr/bin/env bash
# تمرين دخان حي للطبقة 4 (03b §7: تمرين "تجمد كامل" + SIGTERM) — يُشغَّل يدوياً، ليس ضمن pytest.
# يطلق المشرف (SUPERVISED=1)، يجمّد العامل بـ SIGSTOP، ويتأكد أن المشرف قتله وأعاد الإطلاق، ثم SIGTERM نظيف.
# الاستخدام: bash smoke_supervisor.sh   (لينكس/ماك فقط — يعتمد على SIGSTOP)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
T="${TMPDIR:-/tmp}/smoke_sv_$$"; mkdir -p "$T/data"
export DATA_DIR="$T/data" PORT="${PORT:-18670}" HOST=127.0.0.1 SUPERVISED=1 SUPERVISOR_BEAT_MAX=4 WORKER_BEAT_INTERVAL=1 NOTIFY_ON_START=0 SHUTDOWN_GRACE=3
unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
cd "$HERE"
python3 main.py > "$T/log.txt" 2>&1 &
SUP=$!
sleep 5
WORKER=$(grep -o "pid [0-9]*" "$T/log.txt" | head -1 | cut -d' ' -f2)
echo "supervisor=$SUP worker=$WORKER"
curl -s -o /dev/null -w "health1=%{http_code}\n" "http://127.0.0.1:$PORT/health"
sleep 6
echo "--- freezing worker $WORKER (SIGSTOP) ---"
kill -STOP "$WORKER"
sleep 24   # كشف 4ث + grace terminate 10ث + backoff 5ث + إقلاع
WORKER2=$(grep -o "pid [0-9]*" "$T/log.txt" | tail -1 | cut -d' ' -f2)
echo "worker_after_freeze=$WORKER2 (old $WORKER alive? $(kill -0 "$WORKER" 2>/dev/null && echo yes || echo no))"
curl -s "http://127.0.0.1:$PORT/health" | python3 -c "import sys,json; h=json.load(sys.stdin); print('health2 ok=', h['ok'])"
echo "--- SIGTERM supervisor ---"
kill -TERM "$SUP"; wait "$SUP"; echo "supervisor_exit=$?"
kill -0 "$WORKER2" 2>/dev/null && echo "WORKER STILL ALIVE" || echo "worker gone"
echo "=== LOG ==="
grep -E "supervisor|🧊|SIGTERM|👋|🔁" "$T/log.txt" | cut -c1-150
echo; cat "$T/data/state/restart_reason.json" 2>/dev/null; echo
rm -rf "$T"
