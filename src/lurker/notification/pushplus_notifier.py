import requests
from collections.abc import Mapping

PUSHPLUS_URL = "https://www.pushplus.plus/send"


class PushPlusNotifier:
    def __init__(self, token: str):
        self.token = token

    def send(self, title: str, markdown_content: str) -> None:
        payload = {
            "token": self.token,
            "title": title,
            "content": markdown_content,
            "template": "markdown",
        }
        resp = requests.post(PUSHPLUS_URL, json=payload, timeout=20)
        resp.raise_for_status()
        try:
            response_data = resp.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError("PushPlus response is not valid JSON") from exc
        if not isinstance(response_data, Mapping):
            raise RuntimeError("PushPlus response JSON must be an object")
        code = response_data.get("code")
        if code != 200:
            message = response_data.get("msg") or response_data.get("message") or "unknown error"
            raise RuntimeError(f"PushPlus rejected request: code={code}, message={message}")
