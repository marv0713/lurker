from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


PersonalCloseState = dict[str, Any]


class PersonalCloseStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> PersonalCloseState:
        if not self.path.exists():
            return {"schema_version": 1, "accepted_dates": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("personal close state must be a JSON object")
        if raw.get("schema_version") != 1:
            raise ValueError("unsupported personal close state schema")
        if not isinstance(raw.get("accepted_dates"), dict):
            raise ValueError("personal close state accepted_dates must be a mapping")
        return raw

    @staticmethod
    def was_accepted(state: PersonalCloseState, report_date: str) -> bool:
        accepted = state.get("accepted_dates", {})
        return isinstance(accepted, dict) and report_date in accepted

    @staticmethod
    def mark_accepted(
        state: PersonalCloseState,
        report_date: str,
        accepted_at: datetime,
    ) -> None:
        accepted = state.setdefault("accepted_dates", {})
        if not isinstance(accepted, dict):
            raise ValueError("personal close state accepted_dates must be a mapping")
        accepted[report_date] = {"accepted_at": accepted_at.isoformat()}

    def save(self, state: PersonalCloseState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
