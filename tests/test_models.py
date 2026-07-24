from datetime import datetime

import pytest
from pydantic import ValidationError

from kontur.models import EventCreate


def event_data() -> dict[str, object]:
    return {
        "id": "evt_test_001",
        "occurred_at": "2026-07-24T03:12:00+03:00",
        "producer": "test",
        "type": "run_completed",
        "title": "Done",
        "deduplication_key": "test:001",
    }


def test_event_accepts_timezone() -> None:
    event = EventCreate.model_validate(event_data())
    assert event.occurred_at.utcoffset() is not None


def test_event_rejects_naive_datetime() -> None:
    data = event_data()
    data["occurred_at"] = datetime(2026, 7, 24, 3, 12)
    with pytest.raises(ValidationError):
        EventCreate.model_validate(data)


def test_event_rejects_unknown_top_level_fields() -> None:
    data = event_data()
    data["secret"] = "must not pass"
    with pytest.raises(ValidationError):
        EventCreate.model_validate(data)
