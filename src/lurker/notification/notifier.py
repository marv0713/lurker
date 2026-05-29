from typing import Protocol


class Notifier(Protocol):
    def send(self, title: str, markdown_content: str) -> None:
        """Send a notification.
        
        Args:
            title: A short summary title (can be used for PushPlus preview or Email subject).
            markdown_content: The full markdown content of the report.
        """
        ...


class StubNotifier:
    """A stub notifier that does nothing, useful for tests or when no token is provided."""
    
    def send(self, title: str, markdown_content: str) -> None:
        pass
