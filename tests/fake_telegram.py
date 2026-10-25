# -*- coding: utf-8 -*-
"""
FakeTelegram (13 §4.2): يحاكي api.telegram.org داخل الاختبارات بلا شبكة.
- getUpdates يُخدَم من قائمة سيناريو مرتبة: كل عنصر إمّا FakeResp أو استثناء يُرفع.
- بعد نفاد السيناريو يضبط stop_event (إن مُرِّر) ويرجع 200 فارغاً — فتنتهي حلقة الـ poller نظيفة.
- يسجل كل الطلبات (url/params/json/timeout) للتحقق.
"""


class FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {"ok": True, "result": []}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def updates(*messages, start_id=100):
    """FakeResp 200 يحمل رسائل update (كل رسالة dict message)."""
    result = [{"update_id": start_id + i, "message": m} for i, m in enumerate(messages)]
    return FakeResp(200, {"ok": True, "result": result})


class FakeTelegram:
    def __init__(self, script=None, stop_event=None):
        self.script = list(script or [])
        self.stop_event = stop_event
        self.calls = []          # كل الطلبات
        self.sent = []           # sendMessage payloads
        self.exhausted = False

    # ---- واجهة متوافقة مع telegram_bot._http_get/_http_post ----
    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append({"method": "GET", "url": url, "params": params, "timeout": timeout})
        if "getUpdates" in url:
            if self.script:
                item = self.script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            self.exhausted = True
            if self.stop_event is not None:
                self.stop_event.set()
            return FakeResp(200, {"ok": True, "result": []})
        return FakeResp(200, {"ok": True, "result": {"file_path": "voice/x.oga"}})

    def post(self, url, json=None, data=None, files=None, timeout=None, **kw):
        self.calls.append({"method": "POST", "url": url, "json": json, "data": data, "timeout": timeout})
        if "sendMessage" in url:
            self.sent.append(json)
        return FakeResp(200, {"ok": True, "result": {"message_id": 1}})

    def install(self, tb, monkeypatch):
        monkeypatch.setattr(tb, "_http_get", self.get)
        monkeypatch.setattr(tb, "_http_post", self.post)
        return self
