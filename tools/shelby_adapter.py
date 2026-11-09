#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/shelby_adapter.py — محرك وأدابتر شلبي القياسي (CLI) الخالي تماماً من الأسرار.

يستقبل المدخلات عبر stdin ويخرج النص النهائي عبر stdout مع كود خروج 0.
في حال الخطأ يكتب وسوم الفشل المهيكلة على stderr مع كود خروج 1:
  [SHELBY_EMPTY]
  [SHELBY_REQ_FAIL:{err}]
  [SHELBY_HTTP:{status}]
  [SHELBY_DEADLINE]

يقرأ كافة الإعدادات والتوكنات من متغيرات البيئة دون تضمين أي أسرار في الكود:
  SHELBY_URL: رابط الـ API (افتراضياً http://127.0.0.1:8000/v1/chat/completions)
  SHELBY_MODEL: اسم النموذج (افتراضياً chatgpt)
  SHELBY_HTTP_TIMEOUT: المهلة القصوى بالثواني (افتراضياً 180.0)
  PLAY_INTEGRITY_TOKEN / SHELBY_AUTH_TOKEN: توكن المصادقة إن وُجد
  COOKIES / SHELBY_COOKIES: الكوكيز إن وُجدت
"""

import json
import os
import sys
import time

try:
    import requests
except ImportError:
    requests = None


def main():
    payload = sys.stdin.read().strip()
    if not payload:
        sys.stderr.write("[SHELBY_EMPTY]\n")
        sys.exit(1)

    url = os.environ.get("SHELBY_URL", "http://127.0.0.1:8000/v1/chat/completions")
    model = os.environ.get("SHELBY_MODEL", "chatgpt")
    try:
        total_timeout = float(os.environ.get("SHELBY_HTTP_TIMEOUT", "180.0"))
    except ValueError:
        total_timeout = 180.0

    auth_token = os.environ.get("SHELBY_AUTH_TOKEN") or os.environ.get("PLAY_INTEGRITY_TOKEN") or ""
    cookies = os.environ.get("SHELBY_COOKIES") or os.environ.get("COOKIES") or ""

    if requests is None:
        sys.stderr.write("[SHELBY_REQ_FAIL:requests_missing]\n")
        sys.exit(1)

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if cookies:
        headers["Cookie"] = cookies

    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": payload}
        ],
        "stream": True,
    }

    t0 = time.monotonic()
    total_deadline = t0 + total_timeout

    try:
        connect_timeout = min(10.0, total_timeout)
        read_timeout = min(30.0, total_timeout)
        resp = requests.post(
            url,
            json=body,
            headers=headers,
            stream=True,
            timeout=(connect_timeout, read_timeout),
        )
    except Exception as e:
        sys.stderr.write(f"[SHELBY_REQ_FAIL:{type(e).__name__}]\n")
        sys.exit(1)

    if resp.status_code != 200:
        status = resp.status_code
        resp.close()
        sys.stderr.write(f"[SHELBY_HTTP:{status}]\n")
        sys.exit(1)

    output_chunks = []
    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if time.monotonic() > total_deadline:
                sys.stderr.write("[SHELBY_DEADLINE]\n")
                resp.close()
                sys.exit(1)
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("data: "):
                line = line[6:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
                choices = data.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content") or choices[0].get("text") or ""
                    if content:
                        output_chunks.append(content)
            except Exception:
                output_chunks.append(line + "\n")
    except Exception as e:
        sys.stderr.write(f"[SHELBY_REQ_FAIL:{type(e).__name__}]\n")
        sys.exit(1)
    finally:
        resp.close()

    result = "".join(output_chunks).strip()
    if not result:
        sys.stderr.write("[SHELBY_EMPTY]\n")
        sys.exit(1)

    sys.stdout.write(result)
    sys.exit(0)


if __name__ == "__main__":
    main()
