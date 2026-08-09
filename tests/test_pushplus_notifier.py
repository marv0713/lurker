import pytest
import requests

from lurker.notification.pushplus_notifier import PushPlusNotifier


class Response:
    def __init__(self, payload=None, *, http_error=None, json_error=None):
        self.payload = payload
        self.http_error = http_error
        self.json_error = json_error

    def raise_for_status(self):
        if self.http_error:
            raise self.http_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


def test_pushplus_accepts_only_business_code_200(monkeypatch):
    monkeypatch.setattr(
        "lurker.notification.pushplus_notifier.requests.post",
        lambda *args, **kwargs: Response({"code": 200, "msg": "请求成功"}),
    )

    PushPlusNotifier("secret-token").send("标题", "正文")


def test_pushplus_rejects_business_error_without_exposing_token(monkeypatch):
    monkeypatch.setattr(
        "lurker.notification.pushplus_notifier.requests.post",
        lambda *args, **kwargs: Response({"code": 500, "msg": "服务拒绝"}),
    )

    with pytest.raises(RuntimeError, match="code=500.*服务拒绝") as caught:
        PushPlusNotifier("secret-token").send("标题", "正文")
    assert "secret-token" not in str(caught.value)


def test_pushplus_rejects_non_json_response(monkeypatch):
    monkeypatch.setattr(
        "lurker.notification.pushplus_notifier.requests.post",
        lambda *args, **kwargs: Response(json_error=ValueError("not json")),
    )

    with pytest.raises(RuntimeError, match="valid JSON"):
        PushPlusNotifier("secret-token").send("标题", "正文")


def test_pushplus_preserves_http_errors(monkeypatch):
    error = requests.HTTPError("503")
    monkeypatch.setattr(
        "lurker.notification.pushplus_notifier.requests.post",
        lambda *args, **kwargs: Response(http_error=error),
    )

    with pytest.raises(requests.HTTPError, match="503"):
        PushPlusNotifier("secret-token").send("标题", "正文")
