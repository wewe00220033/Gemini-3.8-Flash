# -*- coding: utf-8 -*-
"""
أداة التفريغ الصوتي المحلية للكمبيوتر (Desktop One-Click Transcriber)
- تفرغ أحدث تسجيل تلقائياً أو أي ملف يُسحب عليها
- تستخدم محرك Transcriber الموحد (3.6 Flash ➔ 3.5 Transcribe — 3.8 معطّل)
- تنسخ النص تلقائياً إلى الحافظة (Windows Clipboard)
- تحفظ نسخة نصية بجانب الملف
"""

import sys
import os
import glob
import subprocess
from config import Config
from key_manager import key_manager
from transcriber import transcribe_audio_file

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def copy_to_clipboard(text):
    try:
        process = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
            stdin=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        process.communicate(input=text.encode("utf-8"))
        return True
    except Exception as e:
        print(f"تعذر النسخ للحافظة تلقائياً: {e}")
        return False

def get_latest_recording(recordings_dir):
    if not os.path.exists(recordings_dir):
        return None
    patterns = ["*.m4a", "*.mp3", "*.wav", "*.ogg", "*.aac", "*.flac"]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(os.path.join(recordings_dir, pattern)))
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]

def main():
    recordings_dir = key_manager.data.get("recordings_dir", Config.DEFAULT_RECORDINGS_DIR)

    if len(sys.argv) > 1:
        target_file = sys.argv[1]
    else:
        target_file = get_latest_recording(recordings_dir)
        if not target_file:
            print(f"❌ لم يتم العثور على أي ملفات صوتية في المجلد:\n{recordings_dir}")
            sys.exit(1)

    print("=" * 65)
    print(f"🎙️ تفريغ الملف: {os.path.basename(target_file)}")
    print(f"📂 المسار: {target_file}")
    active_keys = key_manager.get_active_keys()
    print(f"🔑 المفاتيح النشطة في الـ Pool: {len(active_keys)} مشاريع")
    print("=" * 65)

    try:
        print("⏳ جاري قراءة ومعالجة الملف بالذكاء الاصطناعي...")
        text, model, key_name = transcribe_audio_file(target_file)

        # حفظ نسخة نصية بجانب الملف
        txt_path = os.path.splitext(target_file)[0] + ".txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text)

        copied = copy_to_clipboard(text)

        print("\n" + "=" * 65)
        print(f"✅ تم التفريغ بنجاح عبر [{model}] بالمفتاح [{key_name}]:")
        print("=" * 65)
        print(text)
        print("=" * 65)
        if copied:
            print("📋 ✨ تم نسخ النص تلقائياً إلى الحافظة (Clipboard)! تقدر تعمل Paste فوراً.")
        print(f"💾 تم حفظ نسخة نصية في: {txt_path}")
        print("=" * 65 + "\n")

    except Exception as e:
        print(f"\n❌ حدث خطأ أثناء التفريغ: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
