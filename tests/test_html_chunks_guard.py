# -*- coding: utf-8 -*-
"""T0 (DEC-P2-09): حارس _html_chunks — موازنة <pre>، عدم القطع داخل كيان HTML أو تابع عنقودي، صفر فقد أحرف."""
import html
import re
from telegram_bot import _html_chunks

LIMIT = 4000
_PRE_OPEN, _PRE_CLOSE = "<pre>", "</pre>"
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*);")


def _u16(s):
    return len(s.encode("utf-16-le")) // 2


def _long_arabic(lines=120, width=80):
    return "\n".join(("كلمة " * (width // 5)).strip() for _ in range(lines))


def _strip_boundary_pre(chunks):
    """ينزع </pre> المضاف في نهاية كل قطعة غير أخيرة و<pre> المضاف في بداية كل قطعة غير أولى."""
    out = []
    for i, c in enumerate(chunks):
        if i > 0 and c.startswith(_PRE_OPEN):
            c = c[len(_PRE_OPEN):]
        if i < len(chunks) - 1 and c.endswith(_PRE_CLOSE):
            c = c[:-len(_PRE_CLOSE)]
        out.append(c)
    return "".join(out)


def test_short_pre_single_chunk():
    text = f"{_PRE_OPEN}{html.escape('نص قصير & آمن')}{_PRE_CLOSE}"
    assert _html_chunks(text, LIMIT) == [text]


def test_long_pre_splits_balanced_under_limit():
    text = f"{_PRE_OPEN}{html.escape(_long_arabic())}{_PRE_CLOSE}"
    assert _u16(text) > LIMIT * 2
    chunks = _html_chunks(text, LIMIT)
    assert len(chunks) >= 3
    for c in chunks:
        assert _u16(c) <= LIMIT
        assert c.count(_PRE_OPEN) == c.count(_PRE_CLOSE) == 1
        assert c.startswith(_PRE_OPEN) and c.endswith(_PRE_CLOSE)


def test_never_cuts_inside_html_entity():
    # سطر واحد بلا \n ولا مسافات يجبر cut = max_idx عبر كل الإزاحات 0..6
    for pad in range(7):
        body = ("x" * pad) + ("ab&amp;" * 1500) + ("cd&lt;" * 300) + ("ef&#39;" * 300)
        text = f"{_PRE_OPEN}{body}{_PRE_CLOSE}"
        chunks = _html_chunks(text, LIMIT)
        assert len(chunks) >= 3
        for c in chunks:
            inner = c[len(_PRE_OPEN):-len(_PRE_CLOSE)]
            amps = [m.start() for m in re.finditer("&", inner)]
            starts = {m.start() for m in _ENTITY.finditer(inner)}
            assert set(amps) == starts, f"قطعة تحتوي & عارياً أو كياناً مقطوعاً عند pad={pad}"
            assert not inner.endswith("&") and not re.search(r"&[#a-zA-Z0-9]*$", inner)


def test_concat_equals_original_text():
    text = f"{_PRE_OPEN}{html.escape(_long_arabic())}{_PRE_CLOSE}"
    chunks = _html_chunks(text, LIMIT)
    assert _strip_boundary_pre(chunks) == text


def test_emoji_near_boundary_not_split():
    # إيموجي مركب (U+1F5E3 + U+FE0F) مكرر بلا فواصل عبر الإزاحات 0..2 حتى يقع القطع عليه حتماً
    for pad in range(3):
        body = ("x" * pad) + ("🗣️" * 1500)
        text = f"{_PRE_OPEN}{body}{_PRE_CLOSE}"
        chunks = _html_chunks(text, LIMIT)
        for c in chunks:
            assert _u16(c) <= LIMIT
            c.encode("utf-16-le")
            inner = c[len(_PRE_OPEN):-len(_PRE_CLOSE)]
            assert not inner.startswith("\ufe0f"), f"القطع فصل VS16 في بداية القطعة عند pad={pad}"
        assert _strip_boundary_pre(chunks) == text


def test_standalone_modifier_symbols_do_not_trigger_backtrack():
    # ` و ^ مستقلة (Sk) — القطع عليها مسموح ولا يفسد النص
    for pad in range(7):
        body = ("x" * pad) + ("ab`" * 1500)
        text = f"{_PRE_OPEN}{body}{_PRE_CLOSE}"
        chunks = _html_chunks(text, LIMIT)
        assert _strip_boundary_pre(chunks) == text
        for c in chunks:
            assert _u16(c) <= LIMIT
