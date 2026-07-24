from pathlib import Path

import pytest

from kontur.db import Database
from kontur.models import EventCreate, EventStatus


@pytest.fixture
def database(tmp_path: Path) -> Database:
    result = Database(tmp_path / "kontur.sqlite")
    result.initialize()
    return result


def make_event(**overrides: object) -> EventCreate:
    data: dict[str, object] = {
        "id": "evt_test_001",
        "occurred_at": "2026-07-24T03:12:00+03:00",
        "producer": "night-agent",
        "type": "run_completed",
        "severity": "warning",
        "title": "Night run complete",
        "summary": "One task needs attention",
        "requires_action": True,
        "deduplication_key": "night:2026-07-24",
    }
    data.update(overrides)
    return EventCreate.model_validate(data)


def test_create_event_and_delivery(database: Database) -> None:
    event, created = database.create_event(make_event(), frozenset({123}))
    assert created is True
    assert event.status is EventStatus.NEW
    with database.connection() as connection:
        delivery = connection.execute("SELECT * FROM deliveries").fetchone()
    assert delivery is not None
    assert delivery["recipient"] == "123"


def test_create_event_is_idempotent(database: Database) -> None:
    first, first_created = database.create_event(make_event(), frozenset({123}))
    second_event = make_event(id="evt_other_id")
    second, second_created = database.create_event(second_event, frozenset({123}))
    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1


def test_event_id_is_idempotent_across_producers(database: Database) -> None:
    first, first_created = database.create_event(make_event(), frozenset({123}))
    second_event = make_event(
        producer="n8n",
        deduplication_key="n8n:different-key",
    )
    second, second_created = database.create_event(second_event, frozenset({123}))
    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert second.producer == "night-agent"


def test_info_without_action_is_digest_only(database: Database) -> None:
    event = make_event(severity="info", requires_action=False)
    database.create_event(event, frozenset({123}))
    with database.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0


def test_transition_is_validated(database: Database) -> None:
    database.create_event(make_event(), frozenset())
    resolved = database.transition_event(
        "evt_test_001", EventStatus.RESOLVED, actor_id="owner"
    )
    assert resolved is not None
    assert resolved.status is EventStatus.RESOLVED
    with pytest.raises(ValueError):
        database.transition_event("evt_test_001", EventStatus.SEEN, actor_id="owner")
