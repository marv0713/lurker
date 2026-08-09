import json
from datetime import datetime
from zoneinfo import ZoneInfo

from lurker.application.personal_close_state import PersonalCloseStateStore


def test_state_store_round_trips_atomically_and_preserves_unknown_fields(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"schema_version": 1, "accepted_dates": {}, "future": {"keep": True}}),
        encoding="utf-8",
    )
    store = PersonalCloseStateStore(path)
    state = store.load()

    store.mark_accepted(
        state,
        "2026-08-10",
        datetime(2026, 8, 10, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    store.save(state)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["future"] == {"keep": True}
    assert persisted["accepted_dates"]["2026-08-10"]["accepted_at"].startswith(
        "2026-08-10T18:00:00"
    )
    assert store.was_accepted(store.load(), "2026-08-10") is True


def test_missing_state_loads_as_versioned_empty_mapping(tmp_path):
    state = PersonalCloseStateStore(tmp_path / "missing.json").load()

    assert state == {"schema_version": 1, "accepted_dates": {}}


def test_state_rejects_malformed_accepted_dates(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"schema_version": 1, "accepted_dates": []}', encoding="utf-8")

    try:
        PersonalCloseStateStore(path).load()
    except ValueError as exc:
        assert "accepted_dates" in str(exc)
    else:
        raise AssertionError("expected malformed state to fail")
