# -*- coding: utf-8 -*-
"""
خادم الويب والواجهة الصوتية العصرية 360° (Lightweight Web Engine & Voice UI)
- يعمل بـ Pure Python بدون أطر عمل خارجية (Zero-Dependency)
- تسجيل صوت حقيقي مشفر (Real MediaRecorder Audio Blob) مثل تليجرام بالضبط
- واجهة Glassmorphism داكنة مع زر نسخ فوري ومؤقت تفاعلي
- دعم كامل لسحب وإفلات الملفات وإدارة المفاتيح
"""

import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from config import Config
from key_manager import key_manager
from transcriber import transcribe_audio_bytes

HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>منظومة التفريغ الصوتي الذكي | Gemini AI</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&family=JetBrains+Mono:wght@500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #0a0e17;
            --card-bg: rgba(18, 26, 43, 0.75);
            --card-border: rgba(255, 255, 255, 0.1);
            --primary: #3b82f6;
            --primary-glow: rgba(59, 130, 246, 0.4);
            --accent: #10b981;
            --danger: #ef4444;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Cairo', sans-serif;
        }

        body {
            background: radial-gradient(circle at 50% 20%, #152238 0%, var(--bg-dark) 100%);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 30px 15px;
        }

        .container {
            width: 100%;
            max-width: 850px;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }

        /* الهيدر */
        .header {
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 8px;
        }

        .header h1 {
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #60a5fa, #34d399);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .header p {
            color: var(--text-muted);
            font-size: 1rem;
        }

        .badge-bar {
            display: flex;
            gap: 10px;
            margin-top: 6px;
            flex-wrap: wrap;
            justify-content: center;
        }

        .badge {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            color: #38bdf8;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        /* كارت التسجيل الصوتي */
        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            backdrop-filter: blur(16px);
            border-radius: 20px;
            padding: 30px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 20px;
            text-align: center;
        }

        /* زر المايك الدائري العملاق */
        .mic-container {
            position: relative;
            margin: 15px 0;
        }

        .mic-btn {
            width: 110px;
            height: 110px;
            border-radius: 50%;
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
            border: none;
            color: white;
            font-size: 2.5rem;
            cursor: pointer;
            box-shadow: 0 0 30px var(--primary-glow);
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
            z-index: 2;
        }

        .mic-btn:hover {
            transform: scale(1.06);
            box-shadow: 0 0 45px rgba(59, 130, 246, 0.6);
        }

        .mic-btn.recording {
            background: linear-gradient(135deg, #dc2626, #ef4444);
            box-shadow: 0 0 35px rgba(239, 68, 68, 0.6);
            animation: pulse-red 1.5s infinite;
        }

        @keyframes pulse-red {
            0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
            70% { transform: scale(1.05); box-shadow: 0 0 0 25px rgba(239, 68, 68, 0); }
            100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }

        .timer {
            font-size: 1.5rem;
            font-weight: 700;
            color: var(--text-main);
            font-family: 'JetBrains Mono', monospace;
            letter-spacing: 2px;
        }

        .recording-status {
            color: var(--text-muted);
            font-size: 0.95rem;
        }

        /* سحب وإفلات الملفات */
        .dropzone {
            width: 100%;
            border: 2px dashed rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            padding: 16px;
            cursor: pointer;
            transition: all 0.2s;
            color: var(--text-muted);
            font-size: 0.9rem;
        }

        .dropzone:hover {
            border-color: var(--primary);
            color: var(--text-main);
            background: rgba(59, 130, 246, 0.05);
        }

        /* شاشة عرض النتيجة والنسخ */
        .result-box {
            width: 100%;
            background: rgba(10, 15, 28, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 14px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            text-align: right;
            position: relative;
        }

        .result-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            padding-bottom: 10px;
        }

        .result-meta {
            font-size: 0.85rem;
            color: var(--accent);
            display: flex;
            gap: 12px;
        }

        .copy-btn {
            background: var(--primary);
            color: white;
            border: none;
            padding: 6px 16px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9rem;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: 0.2s;
        }

        .copy-btn:hover {
            background: #2563eb;
        }

        .transcription-text {
            font-size: 1.15rem;
            line-height: 1.8;
            color: #f1f5f9;
            white-space: pre-wrap;
            min-height: 80px;
            user-select: text;
        }

        /* إدارة المفاتيح والحالة السريعة */
        .keys-section {
            width: 100%;
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            text-align: right;
        }

        .add-key-form {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }

        .add-key-form input {
            flex: 1;
            min-width: 250px;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.15);
            padding: 10px 14px;
            border-radius: 8px;
            color: white;
            font-size: 0.9rem;
        }

        .add-key-form button {
            background: var(--accent);
            border: none;
            color: white;
            padding: 10px 20px;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
            transition: 0.2s;
        }

        .add-key-form button:hover {
            background: #059669;
        }

        /* مؤشر التحميل */
        .spinner {
            display: none;
            width: 30px;
            height: 30px;
            border: 3px solid rgba(255, 255, 255, 0.2);
            border-top-color: var(--primary);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 10px auto;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🎙️ منظومة التفريغ الصوتي الذكي 360°</h1>
            <p>تسجيل صوتي حقيقي مشفر (Real Audio) مدعوم بثلاثي Gemini الفائق (3.8 | 3.6 | 3.5 Transcribe)</p>
            <div class="badge-bar">
                <span class="badge">⚡ Zero-Compacting Audio</span>
                <span class="badge">🧠 فهم اللهجة العامية + إنجليزي سليم</span>
                <span class="badge">🔄 تدوير المفاتيح التلقائي (Key Pool)</span>
            </div>
        </div>

        <div class="card">
            <div class="mic-container">
                <button id="micBtn" class="mic-btn" onclick="toggleRecording()">🎤</button>
            </div>
            <div id="timer" class="timer">00:00</div>
            <div id="statusText" class="recording-status">اضغط على المايك لبدء التسجيل الصوتي الحقيقي</div>

            <div class="spinner" id="spinner"></div>

            <div class="dropzone" onclick="document.getElementById('fileInput').click()">
                📁 أو اضغط هنا لرفع أي ملف صوتي من جهازك (m4a, mp3, wav, ogg)
                <input type="file" id="fileInput" accept="audio/*" style="display:none" onchange="handleFileUpload(event)">
            </div>
        </div>

        <div class="result-box">
            <div class="result-header">
                <div class="result-meta">
                    <span id="modelUsed">🤖 الموديل: في الانتظار</span>
                    <span id="keyUsed">🔑 المفتاح: -</span>
                </div>
                <button class="copy-btn" onclick="copyResult()">📋 نسخ النص</button>
            </div>
            <div id="transcriptionResult" class="transcription-text">نص التفريغ سيظهر هنا فور انتهاء التسجيل...</div>
        </div>

        <div class="keys-section">
            <h3>🔑 إدارة مصفوفة المفاتيح (Key Pool)</h3>
            <p style="color:var(--text-muted); font-size:0.85rem">أضف أي مفتاح Gemini جديد لزيادة رصيدك اليومي فوراً بمعدل 65 ريكورد لكل مشروع:</p>
            <form class="add-key-form" onsubmit="addNewKey(event)">
                <input type="text" id="newKeyName" placeholder="اسم المشروع (اختياري)">
                <input type="text" id="newKeyValue" placeholder="مفتاح API (يبدأ بـ AQ. أو AIzaSy)" required>
                <button type="submit">➕ إضافة المفتاح</button>
            </form>
            <div id="keysStatusText" style="font-size:0.85rem; color:#38bdf8; margin-top:4px;"></div>
        </div>
    </div>

    <script>
        let mediaRecorder;
        let audioChunks = [];
        let isRecording = false;
        let startTime;
        let timerInterval;

        async function toggleRecording() {
            if (!isRecording) {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    audioChunks = [];
                    mediaRecorder = new MediaRecorder(stream);

                    mediaRecorder.ondataavailable = event => {
                        if (event.data.size > 0) audioChunks.push(event.data);
                    };

                    mediaRecorder.onstop = sendAudioToServer;

                    mediaRecorder.start();
                    isRecording = true;
                    document.getElementById('micBtn').classList.add('recording');
                    document.getElementById('statusText').innerText = "🔴 جاري التسجيل الصوتي الحقيقي... اضغط للإيقاف والتفريغ";

                    startTime = Date.now();
                    timerInterval = setInterval(updateTimer, 1000);
                } catch (err) {
                    alert("تعذر الوصول للمايكروفون: " + err.message);
                }
            } else {
                mediaRecorder.stop();
                mediaRecorder.stream.getTracks().forEach(track => track.stop());
                isRecording = false;
                document.getElementById('micBtn').classList.remove('recording');
                document.getElementById('statusText').innerText = "⏳ جاري إرسال التسجيل والتفريغ بالذكاء الاصطناعي...";
                clearInterval(timerInterval);
                document.getElementById('spinner').style.display = 'block';
            }
        }

        function updateTimer() {
            const elapsed = Math.floor((Date.now() - startTime) / 1000);
            const mins = String(Math.floor(elapsed / 60)).padStart(2, '0');
            const secs = String(elapsed % 60).padStart(2, '0');
            document.getElementById('timer').innerText = `${mins}:${secs}`;
        }

        async function sendAudioToServer() {
            const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
            const formData = new FormData();
            formData.append('audio', audioBlob, 'record.webm');

            try {
                const resp = await fetch('/api/transcribe', {
                    method: 'POST',
                    body: formData
                });
                const result = await resp.json();
                handleResult(result);
            } catch (err) {
                alert("حدث خطأ في الاتصال بالسيرفر: " + err.message);
                document.getElementById('spinner').style.display = 'none';
            }
        }

        async function handleFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            document.getElementById('statusText').innerText = `⏳ جاري رفع وتفريغ: ${file.name}...`;
            document.getElementById('spinner').style.display = 'block';

            const formData = new FormData();
            formData.append('audio', file, file.name);

            try {
                const resp = await fetch('/api/transcribe', {
                    method: 'POST',
                    body: formData
                });
                const result = await resp.json();
                handleResult(result);
            } catch (err) {
                alert("خطأ: " + err.message);
                document.getElementById('spinner').style.display = 'none';
            }
        }

        function handleResult(data) {
            document.getElementById('spinner').style.display = 'none';
            document.getElementById('statusText').innerText = "✅ تم التفريغ بنجاح! اضغط نسخ بالأعلى لاستخدامه.";

            if (data.ok) {
                document.getElementById('transcriptionResult').innerText = data.text;
                document.getElementById('modelUsed').innerText = `🤖 الموديل: ${data.model}`;
                document.getElementById('keyUsed').innerText = `🔑 المفتاح: ${data.key_name}`;
                // نسخ للحافظة تلقائياً
                navigator.clipboard.writeText(data.text);
            } else {
                document.getElementById('transcriptionResult').innerText = "❌ خطأ: " + data.error;
            }
        }

        function copyResult() {
            const text = document.getElementById('transcriptionResult').innerText;
            navigator.clipboard.writeText(text);
            const btn = document.querySelector('.copy-btn');
            btn.innerText = "✨ تم النسخ!";
            setTimeout(() => { btn.innerText = "📋 نسخ النص"; }, 2000);
        }

        async function addNewKey(e) {
            e.preventDefault();
            const name = document.getElementById('newKeyName').value.trim();
            const key = document.getElementById('newKeyValue').value.trim();

            const resp = await fetch('/api/add_key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key, name })
            });
            const res = await resp.json();
            if (res.ok) {
                document.getElementById('keysStatusText').innerText = `✅ تم بنجاح إضافة [${res.key.name}] لمصفوفة المفاتيح!`;
                document.getElementById('newKeyName').value = '';
                document.getElementById('newKeyValue').value = '';
            } else {
                alert("خطأ: " + res.error);
            }
        }
    </script>
</body>
</html>
"""

class WebRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif self.path == "/api/status":
            summary = key_manager.get_status_summary()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "summary": summary}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/transcribe":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)

                # استخراج ملف الصوت من multipart/form-data ببساطة
                content_type = self.headers.get("Content-Type", "")
                audio_bytes = body
                mime_type = "audio/webm"

                if "boundary=" in content_type:
                    boundary = content_type.split("boundary=")[1].encode()
                    parts = body.split(b"--" + boundary)
                    for part in parts:
                        if b"filename=" in part:
                            header_end = part.find(b"\r\n\r\n")
                            if header_end != -1:
                                headers_part = part[:header_end].decode("latin-1")
                                if "Content-Type:" in headers_part:
                                    mime_type = headers_part.split("Content-Type:")[1].split("\r\n")[0].strip()
                                audio_bytes = part[header_end + 4:].rstrip(b"\r\n")
                                break

                text, model, key_name = transcribe_audio_bytes(audio_bytes, mime_type)
                resp_data = {"ok": True, "text": text, "model": model, "key_name": key_name}
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(resp_data, ensure_ascii=False).encode("utf-8"))

            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))

        elif self.path == "/api/add_key":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                key = payload.get("key", "").strip()
                name = payload.get("name", "").strip() or None

                if not key:
                    raise ValueError("المفتاح فارغ!")

                entry, is_new = key_manager.add_key(key, name)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "key": entry, "is_new": is_new}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))

    def log_message(self, format, *args):
        # كتم سجلات الـ HTTP لعدم التشويش على الكونسول إلا في الأخطاء
        return

def run_web_server():
    server_address = (Config.WEB_HOST, Config.WEB_PORT)
    httpd = HTTPServer(server_address, WebRequestHandler)
    print(f"🌐 واجهة الويب الصوتية الذكية تعمل على: http://{Config.WEB_HOST}:{Config.WEB_PORT}")
    httpd.serve_forever()

def start_web_server_thread():
    t = threading.Thread(target=run_web_server, daemon=True)
    t.start()
    return t
