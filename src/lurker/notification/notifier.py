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


class CompositeNotifier:
    """Send the same notification through multiple providers."""

    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, title: str, markdown_content: str) -> None:
        errors: list[str] = []
        for notifier in self.notifiers:
            try:
                notifier.send(title=title, markdown_content=markdown_content)
            except Exception as exc:
                errors.append(f"{type(notifier).__name__}: {type(exc).__name__}: {exc}")
        if errors:
            raise RuntimeError("; ".join(errors))
