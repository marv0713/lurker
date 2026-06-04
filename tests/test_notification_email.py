from lurker.notification.email_notifier import EmailNotifier, build_email_message
from lurker.notification.notifier import CompositeNotifier


class FakeSMTP:
    sent_messages = []

    def __init__(self, host, port, timeout=20):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.sent_messages.append(message)


def test_build_email_message_contains_text_and_html_parts():
    message = build_email_message(
        subject="职业资金雷达日报",
        markdown_content="# 标题\n\n- A",
        sender="from@example.com",
        recipients=["to@example.com"],
    )

    assert message["Subject"] == "职业资金雷达日报"
    assert message["From"] == "from@example.com"
    assert message["To"] == "to@example.com"
    assert "plain" in message.get_body(preferencelist=("plain",)).get_content_type()
    assert "html" in message.get_body(preferencelist=("html",)).get_content_type()


def test_email_notifier_sends_via_injected_smtp_class():
    FakeSMTP.sent_messages = []
    notifier = EmailNotifier(
        host="smtp.example.com",
        port=587,
        username="user",
        password="pass",
        sender="from@example.com",
        recipients=["to@example.com"],
        smtp_class=FakeSMTP,
    )

    notifier.send("日报", "# 内容")

    assert len(FakeSMTP.sent_messages) == 1
    assert FakeSMTP.sent_messages[0]["Subject"] == "日报"


def test_composite_notifier_sends_to_all_providers():
    calls = []

    class Provider:
        def __init__(self, name):
            self.name = name

        def send(self, title, markdown_content):
            calls.append((self.name, title, markdown_content))

    notifier = CompositeNotifier([Provider("push"), Provider("email")])

    notifier.send("title", "body")

    assert calls == [("push", "title", "body"), ("email", "title", "body")]
