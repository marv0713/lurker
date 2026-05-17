import requests


PUSHPLUS_URL = "https://www.pushplus.plus/send"


def build_pushplus_payload(token: str, title: str, content: str) -> dict[str, str]:
    return {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
    }


def send_pushplus(token: str, title: str, content: str) -> requests.Response:
    payload = build_pushplus_payload(token=token, title=title, content=content)
    return requests.post(PUSHPLUS_URL, json=payload, timeout=20)
