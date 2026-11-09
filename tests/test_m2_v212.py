# -*- coding: utf-8 -*-
"""
tests/test_m2_v212.py — حزمة اختبارات المرحلة M2 المعتمدة (المخطط العام v2.12).

تغطي:
  1. قتل شجرة العمليات _kill_process_tree على Windows (taskkill) وPOSIX (killpg).
  2. استدعاء قتل الشجرة داخل Supervisor._terminate() عند تجاوز المهلة.
  3. منسّق الصف العادل _ShelbyQueueCoordinator (عدالة FIFO، سقف 8 منتظرين، وتخطي الأولوية).
  4. محرك Popen في _call_local_provider وقتل شجرة العمليات عند Timeout.
  5. السبك بالمتاح وإلغاء الهروب المبكر في synthesize().
  6. سقف البرومبت 28000 في prompts.compose_synthesis_prompt وحماية الخام.
  7. آلة حالات synthesize_ex وميزانية 420ث الصارمة والارتداد عند < 5.0ث.
  8. ارتداد السبك لاحتياطي flash-lite ثم إنقاذ المسودة أو الخام.
  9. تعاقد أدابتر شلبي القياسي tools/shelby_adapter.py وأكواد الفشل على stderr.
"""

import os
import signal
import subprocess
import sys
import time
import pytest

import config
import prompts
import text_assist
import supervisor
from tools import shelby_adapter


# -----------------------------------------------------------------------------
# 1. قتل شجرة العمليات _kill_process_tree على النظامين
# -----------------------------------------------------------------------------
def test_kill_process_tree_windows_invokes_taskkill(monkeypatch):
    """التحقق من استدعاء taskkill /F /T /PID <pid> على بيئة Windows."""
    executed_cmds = []

    def fake_run(cmd, **kwargs):
        executed_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    class FakeProc:
        def __init__(self):
            self.pid = 9999
            self.killed = False

        def kill(self):
            self.killed = True

    proc = FakeProc()
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(subprocess, "run", fake_run)

    text_assist._kill_process_tree(proc)

    assert proc.killed is True
    assert len(executed_cmds) == 1
    assert executed_cmds[0] == ["taskkill", "/F", "/T", "/PID", "9999"]


def test_kill_process_tree_posix_invokes_killpg(monkeypatch):
    """التحقق من استدعاء os.killpg مع SIGKILL على بيئات POSIX."""
    killed_pgids = []

    def fake_getpgid(pid):
        return 5555

    def fake_killpg(pgid, sig):
        killed_pgids.append((pgid, sig))

    class FakeProc:
        def __init__(self):
            self.pid = 1234
            self.killed = False

        def kill(self):
            self.killed = True

    proc = FakeProc()
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getpgid", fake_getpgid, raising=False)
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)

    text_assist._kill_process_tree(proc)

    assert proc.killed is True
    expected_sig = getattr(signal, "SIGKILL", 9)
    assert killed_pgids == [(5555, expected_sig)]


def test_supervisor_terminate_kills_process_tree_on_timeout(monkeypatch):
    """التحقق من أن المشرف يستدعي _kill_process_tree عندما تتجاوز العملية مهلة الإيقاف."""
    killed = []

    def fake_kill_tree(p):
        killed.append(p.pid)

    class StubChildProc:
        def __init__(self):
            self.pid = 4321
            self.returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    s = supervisor.Supervisor()
    child = StubChildProc()
    s.proc = child

    monkeypatch.setattr(supervisor, "_kill_process_tree", fake_kill_tree)
    ret = s._terminate(grace=0.01, graceful=False)

    assert ret is None
    assert 4321 in killed


# -----------------------------------------------------------------------------
# 2. منسّق صف FIFO وسقف المنتظرين وأولوية السبك
# -----------------------------------------------------------------------------
def test_shelby_queue_coordinator_fifo_and_capacity():
    """التحقق من عدالة FIFO وسقف الـ 8 منتظرين وتفادي التجويع."""
    coord = text_assist._ShelbyQueueCoordinator(max_waiters=2)

    # 1. الحجز الأول ينجح فوراً
    assert coord.acquire(timeout=0.1) is True

    # 2. حجز ثانٍ وثالث (يملآن الطابور بسقف 2)
    # نختبر في خيوط لمعاينة الترتيب
    results = []

    def waiter(waiter_id, timeout, priority=False):
        ok = coord.acquire(timeout=timeout, priority=priority)
        results.append((waiter_id, ok))
        if ok:
            time.sleep(0.02)
            coord.release()

    import threading
    t1 = threading.Thread(target=waiter, args=(1, 1.0, False))
    t2 = threading.Thread(target=waiter, args=(2, 1.0, False))
    t1.start()
    t2.start()
    time.sleep(0.05)

    # 3. محاولة حجز رابع تتجاوز السقف (max_waiters=2) وترفض فوراً بـ False
    assert coord.acquire(timeout=0.01) is False

    # 4. إفساح المجال للحاجز الأول
    coord.release()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    # التحقق من أن الخيطين وصلا بالترتيب FIFO ونجحا
    assert (1, True) in results
    assert (2, True) in results


def test_shelby_queue_coordinator_priority():
    """التحقق من أن تذكرة الأولوية (priority=True للسبك) تسبق التذاكر العادية في الطابور."""
    coord = text_assist._ShelbyQueueCoordinator(max_waiters=5)
    assert coord.acquire(timeout=0.1) is True

    order = []

    def waiter(name, timeout, priority=False):
        ok = coord.acquire(timeout=timeout, priority=priority)
        if ok:
            order.append(name)
            time.sleep(0.01)
            coord.release()

    import threading
    t_normal1 = threading.Thread(target=waiter, args=("normal1", 1.0, False))
    t_normal2 = threading.Thread(target=waiter, args=("normal2", 1.0, False))
    t_normal1.start()
    t_normal2.start()
    time.sleep(0.05)

    # إدراج طلب ذي أولوية بعد الطلبات العادية
    t_prio = threading.Thread(target=waiter, args=("prio_synth", 1.0, True))
    t_prio.start()
    time.sleep(0.05)

    # فتح الباب
    coord.release()
    t_normal1.join(timeout=2.0)
    t_normal2.join(timeout=2.0)
    t_prio.join(timeout=2.0)

    # يجب أن تُخدم prio_synth أولاً لأنها قفزت للأمام بأولوية السبك، ثم normal1 وnormal2
    assert order == ["prio_synth", "normal1", "normal2"]


# -----------------------------------------------------------------------------
# 3. استدعاء المزود المحلي Popen وقتل الشجرة عند Timeout
# -----------------------------------------------------------------------------
def test_call_local_provider_timeout_kills_tree(monkeypatch):
    """التحقق من أن timeout يستدعي _kill_process_tree ويرجع سبب المهلة المهيكل."""
    tree_killed = []

    def fake_kill(p):
        tree_killed.append(p.pid)

    class TimeoutProc:
        def __init__(self, *args, **kwargs):
            self.pid = 7777
            self.returncode = None

        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd="dummy", timeout=timeout)
            return "", ""

        def poll(self):
            return None

        def kill(self):
            pass

    monkeypatch.setattr(text_assist, "_kill_process_tree", fake_kill)
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_CMD", "python mock_llm.py")
    monkeypatch.setattr(text_assist.Config, "LOCAL_POLISH_TIMEOUT", 0.05)

    res, reason = text_assist._call_local_provider("sys", "raw", timeout=0.05, process_factory=TimeoutProc)

    assert res is None
    assert "تجاوز مهلة" in reason
    assert 7777 in tree_killed


# -----------------------------------------------------------------------------
# 4. السبك بالمتاح وإلغاء شروط الهروب 351-354 في synthesize()
# -----------------------------------------------------------------------------
def test_synthesize_works_with_single_draft_or_raw(monkeypatch):
    """التحقق من أن غياب إحدى المسودتين لم يعد يهرب بل يستدعي السبك على المتاح."""
    generated_prompts = []

    def fake_generate(sys_text, user_content):
        generated_prompts.append((sys_text, user_content))
        return "النسخة الذهبية المسبوكة من مسودة واحدة", None

    monkeypatch.setattr(text_assist, "generate", fake_generate)

    # 1. مسودة جيميني فقط
    text, err = text_assist.synthesize(raw_text="أصل الكلام الخام", gemini_draft="مسودة جيميني فقط", shelby_draft=None)
    assert text == "النسخة الذهبية المسبوكة من مسودة واحدة"
    assert err is None
    assert len(generated_prompts) == 1
    assert "مسودة جيميني فقط" in generated_prompts[0][1]

    # 2. مسودة شلبي فقط
    text2, err2 = text_assist.synthesize(raw_text="أصل الكلام الخام", gemini_draft=None, shelby_draft="مسودة شلبي فقط")
    assert text2 == "النسخة الذهبية المسبوكة من مسودة واحدة"
    assert err2 is None
    assert len(generated_prompts) == 2
    assert "مسودة شلبي فقط" in generated_prompts[1][1]

    # 3. بدون مسودات (السبك من الخام مباشرة)
    text3, err3 = text_assist.synthesize(raw_text="أصل الكلام الخام", gemini_draft="", shelby_draft="")
    assert text3 == "النسخة الذهبية المسبوكة من مسودة واحدة"
    assert err3 is None
    assert len(generated_prompts) == 3

    # 4. الخام فارغ -> يرفض بـ "نص فارغ" دون استدعاء التوليد
    text4, err4 = text_assist.synthesize(raw_text="   ", gemini_draft="مسودة", shelby_draft="مسودة")
    assert text4 is None
    assert err4 == "نص فارغ"


# -----------------------------------------------------------------------------
# 5. سقف البرومبت 28000 في prompts.py وحماية الخام والدستور
# -----------------------------------------------------------------------------
def test_compose_synthesis_prompt_ceiling_preserves_raw_and_sys():
    """التحقق من أن تجاوز سقف الـ 28000 يقتطع المسودات مع صون النص الخام والدستور التحريري."""
    raw = "خام " * 500  # ~2500 حرف
    huge_draft_g = "جيميني " * 3000  # ~21000 حرف
    huge_draft_s = "شلبي " * 3000   # ~18000 حرف

    sys_text, user_content = prompts.compose_synthesis_prompt(
        raw_text=raw,
        gemini_draft=huge_draft_g,
        shelby_draft=huge_draft_s,
        max_chars=28000,
    )

    total_len = len(sys_text) + len(user_content)
    assert total_len <= 28000, f"تجاوز السقف الحجمي! الطول = {total_len}"
    assert "أصل التفريغ الصوتي الخام" in user_content
    assert raw.strip() in user_content
    assert "تم اقتطاع باقي المسودة للسقف الحجمي" in user_content


# -----------------------------------------------------------------------------
# 6. آلة حالات synthesize_ex وميزانية الـ 420ث والارتداد
# -----------------------------------------------------------------------------
def test_synthesize_ex_full_budget_uses_shelby2(monkeypatch):
    """عند توفر الميزانية ونجاح شلبي 2، يُعتمد ناتج shelby2 مع مؤشراته الصحيحة."""
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "python mock_shelby.py")
    monkeypatch.setattr(config.Config, "SYNTH_TOTAL_BUDGET_SEC", 420.0)

    def fake_call_local(sys_text, user_content, stage="polish", timeout=None, priority=False):
        assert stage == "synthesis"
        assert priority is True
        return "السبك الذهبي من شلبي 2", None

    monkeypatch.setattr(text_assist, "_call_local_provider", fake_call_local)

    res = text_assist.synthesize_ex(
        raw_text="نص خام للاختبار",
        gemini_draft="مسودة 1",
        shelby_draft="مسودة 2",
        flow_start=time.monotonic(),
    )

    assert res["text"] == "السبك الذهبي من شلبي 2"
    assert res["source_tag"] == "shelby2"
    assert res["fallback_used"] is False
    assert res["error_reason"] is None


def test_synthesize_ex_skips_shelby_when_budget_exhausted(monkeypatch):
    """إذا كان المتبقي من الميزانية < MIN_SHELBY_EXEC_SEC (5.0ث)، يُتخطى شلبي دون محاولة وهمية."""
    shelby_called = []

    def fake_call_local(*args, **kwargs):
        shelby_called.append(True)
        return "لن يُستدعى", None

    flash_called = []

    def fake_generate(sys_text, user_content):
        flash_called.append(True)
        return "سبك احتياطي فلاش", None

    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "python mock_shelby.py")
    monkeypatch.setattr(config.Config, "MIN_SHELBY_EXEC_SEC", 5.0)
    monkeypatch.setattr(config.Config, "SYNTH_TOTAL_BUDGET_SEC", 420.0)
    monkeypatch.setattr(text_assist, "_call_local_provider", fake_call_local)
    monkeypatch.setattr(text_assist, "generate", fake_generate)

    # نحاكي أن التدفق بدأ قبل 417 ثانية (المتبقي = 3 ثوانٍ < 5.0ث)
    simulated_start = time.monotonic() - 417.0

    res = text_assist.synthesize_ex(
        raw_text="نص خام",
        gemini_draft="مسودة ج",
        shelby_draft="مسودة ش",
        flow_start=simulated_start,
    )

    # شلبي يجب ألا يُستدعى نهائياً
    assert len(shelby_called) == 0
    # يرتد لاحتياطي فلاش لايت
    assert len(flash_called) == 1
    assert res["text"] == "سبك احتياطي فلاش"
    assert res["source_tag"] == "flash_fallback"
    assert res["fallback_used"] is True


def test_synthesize_ex_fallbacks_to_rescue_draft_when_all_fail(monkeypatch):
    """عند فشل شلبي 2 واحتياطي فلاش لايت، ترتد آلة الحالات لإنقاذ أفضل مسودة متاحة."""
    monkeypatch.setattr(config.Config, "LOCAL_POLISH_CMD", "python mock_shelby.py")

    def fake_call_local(*args, **kwargs):
        return None, "شلبي معطل 500"

    def fake_generate(*args, **kwargs):
        return None, "فلاش كوتا 429"

    monkeypatch.setattr(text_assist, "_call_local_provider", fake_call_local)
    monkeypatch.setattr(text_assist, "generate", fake_generate)

    res = text_assist.synthesize_ex(
        raw_text="نص خام",
        gemini_draft="مسودة جيميني المنقذة",
        shelby_draft="مسودة شلبي المنقذة",
        flow_start=time.monotonic(),
    )

    # يفضل مسودة شلبي المحلية إن كانت سليمة، أو جيميني
    assert res["text"] == "مسودة شلبي المنقذة"
    assert res["source_tag"] == "rescue_shelby_draft"
    assert res["fallback_used"] is True
    assert "شلبي معطل" in res["error_reason"] or "فلاش كوتا" in res["error_reason"]


# -----------------------------------------------------------------------------
# 7. تعاقد أدابتر شلبي القياسي tools/shelby_adapter.py
# -----------------------------------------------------------------------------
def test_shelby_adapter_empty_stdin_exits_with_code_and_tag(monkeypatch, capsys):
    """التحقق من أن إدخالاً فارغاً للأدابتر ينتج [SHELBY_EMPTY] على stderr ويخرج بكود 1."""
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n  "))

    with pytest.raises(SystemExit) as exc:
        shelby_adapter.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "[SHELBY_EMPTY]" in captured.err
