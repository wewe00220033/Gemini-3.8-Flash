#!/usr/bin/env bash
# تشغيل المنظومة (ويب + بوت) على Linux/Mac أو استضافة تدعم أوامر shell.
# المتغيرات تُقرأ من البيئة أو من ملف .env بجانب الكود.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f requirements.txt ] && ! python3 -c "import requests" 2>/dev/null; then
  python3 -m pip install -q -r requirements.txt
fi
exec python3 main.py
