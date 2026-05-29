import requests

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
