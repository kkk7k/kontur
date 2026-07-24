from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kontur.db import Database
from kontur.models import EventCreate
from kontur.service import KonturService


def service(tmp_path: Path) -> KonturService:
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    return KonturService(database, frozenset({123}), timezone="Europe/Moscow")


def test_empty_day_does_not_create_digest(tmp_path: Path) -> None:
    event, created = service(tmp_path).create_daily_digest(
        producer="n8n",
        now=datetime(2026, 7, 24, 8, 30, tzinfo=ZoneInfo("Europe/Moscow")),
    )
    assert event is None
    assert created is False


def test_digest_aggregates_actionable_events_and_is_idempotent(tmp_path: Path) -> None:
    app = service(tmp_path)
    app.register_event(
        EventCreate.model_validate(
            {
                "id": "evt_actionable",
                "occurred_at": "2026-07-24T07:00:00+03:00",
                "producer": "career",
                "type": "opportunity_found",
                "severity": "warning",
                "title": "Senior Go vacancy",
                "requires_action": True,
                "deduplication_key": "career:vacancy:1",
            }
        )
    )
    now = datetime(2026, 7, 24, 8, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    first, first_created = app.create_daily_digest(producer="n8n", now=now)
    second, second_created = app.create_daily_digest(producer="n8n", now=now)
    assert first is not None
    assert first_created is True
    assert first.metrics["events_total"] == 1
    assert first.metrics["actionable_total"] == 1
    assert first.requires_action is True
    assert second is not None
    assert second_created is False
    assert second.id == first.id


def test_digest_ignores_previous_digest(tmp_path: Path) -> None:
    app = service(tmp_path)
    app.register_event(
        EventCreate.model_validate(
            {
                "id": "evt_info",
                "occurred_at": "2026-07-24T07:00:00+03:00",
                "producer": "night-agent",
                "type": "run_completed",
                "title": "Done",
                "deduplication_key": "night:done",
            }
        )
    )
    now = datetime(2026, 7, 24, 8, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    first, _ = app.create_daily_digest(producer="n8n", now=now)
    second, _ = app.create_daily_digest(producer="n8n", now=now)
    assert first is not None
    assert second is not None
    assert second.metrics["events_total"] == 1
