import requests
from markdown_it import MarkdownIt

PUSHPLUS_URL = "https://www.pushplus.plus/send"

_HTML_TEMPLATE = """
<html>
<head>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; color: #333; line-height: 1.6; padding: 15px; font-size: 15px; background-color: #f9f9f9; }}
  .container {{ background-color: #fff; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  h1 {{ font-size: 20px; color: #1a73e8; border-bottom: 2px solid #eee; padding-bottom: 8px; margin-top: 0; }}
  h2 {{ font-size: 17px; color: #202124; margin-top: 20px; margin-bottom: 10px; border-left: 4px solid #1a73e8; padding-left: 8px; }}
  h3 {{ font-size: 15px; color: #444; margin-top: 15px; }}
  ul {{ padding-left: 20px; margin-top: 5px; }}
  li {{ margin-bottom: 6px; }}
  strong {{ color: #d93025; font-weight: 600; }}
  blockquote {{ border-left: 4px solid #ddd; padding-left: 12px; color: #666; margin-left: 0; background: #f5f5f5; padding-top: 8px; padding-bottom: 8px; border-radius: 4px; }}
  p {{ margin-top: 8px; margin-bottom: 8px; }}
</style>
</head>
<body>
<div class="container">
{content}
</div>
</body>
</html>
"""


class PushPlusNotifier:
    def __init__(self, token: str):
        self.token = token
        self.md = MarkdownIt()

    def send(self, title: str, markdown_content: str) -> None:
        html_body = self.md.render(markdown_content)
        html_content = _HTML_TEMPLATE.format(content=html_body)
        
        payload = {
            "token": self.token,
            "title": title,
            "content": html_content,
            "template": "html",
        }
        resp = requests.post(PUSHPLUS_URL, json=payload, timeout=20)
        resp.raise_for_status()
