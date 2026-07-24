import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kontur.db import Database
from kontur.models import EventCreate, EventStatus
from kontur.worker import DeliveryWorker


class SuccessfulBot:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []

    async def send_message(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(message_id=42)

    async def send_document(self, **kwargs: object) -> SimpleNamespace:
        self.documents.append(kwargs)
        return SimpleNamespace(message_id=43)


class FailingBot:
    async def send_message(self, **_: object) -> None:
        raise ConnectionError("secret details must not be stored")

    async def send_document(self, **_: object) -> None:
        raise AssertionError("must not send document after message failure")


def database_with_event(tmp_path: Path) -> Database:
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    database.create_event(
        EventCreate.model_validate(
            {
                "id": "evt_delivery",
                "occurred_at": "2026-07-24T03:12:00+03:00",
                "producer": "test",
                "type": "run_failed",
                "severity": "error",
                "title": "Failed",
                "deduplication_key": "test:delivery",
            }
        ),
        frozenset({123}),
    )
    return database


@pytest.mark.asyncio
async def test_successful_delivery_is_marked_sent(tmp_path: Path) -> None:
    database = database_with_event(tmp_path)
    worker = DeliveryWorker(database, SuccessfulBot())  # type: ignore[arg-type]
    assert await worker.deliver_once() == 1
    with database.connection() as connection:
        delivery = connection.execute("SELECT * FROM deliveries").fetchone()
    assert delivery["status"] == "sent"
    assert delivery["external_message_id"] == "42"
    assert database.get_event("evt_delivery").status is EventStatus.NOTIFIED


@pytest.mark.asyncio
async def test_failed_delivery_stores_safe_error_only(tmp_path: Path) -> None:
    database = database_with_event(tmp_path)
    worker = DeliveryWorker(database, FailingBot())  # type: ignore[arg-type]
    assert await worker.deliver_once() == 1
    with database.connection() as connection:
        delivery = connection.execute("SELECT * FROM deliveries").fetchone()
    assert delivery["status"] == "retry"
    assert delivery["last_error_safe"] == "ConnectionError"


@pytest.mark.asyncio
async def test_allowed_artifact_is_sent(tmp_path: Path) -> None:
    report = tmp_path / "morning-report.md"
    report.write_text("# Report", encoding="utf-8")
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    database.create_event(
        EventCreate.model_validate(
            {
                "id": "evt_artifact",
                "occurred_at": "2026-07-24T03:12:00+03:00",
                "producer": "night-agent",
                "type": "run_completed",
                "severity": "warning",
                "title": "Report ready",
                "requires_action": True,
                "deduplication_key": "test:artifact",
                "artifacts": [
                    {
                        "kind": "markdown",
                        "title": "Morning report",
                        "uri": str(report),
                    }
                ],
            }
        ),
        frozenset({123}),
    )
    bot = SuccessfulBot()
    worker = DeliveryWorker(
        database,
        bot,  # type: ignore[arg-type]
        artifact_allowed_roots=(tmp_path,),
    )
    await worker.deliver_once()
    assert len(bot.documents) == 1


@pytest.mark.asyncio
async def test_artifact_outside_allowlist_is_not_sent(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private", encoding="utf-8")
    database = database_with_event(tmp_path)
    event = database.get_event("evt_delivery")
    assert event is not None
    with database.connection() as connection:
        connection.execute(
            """
            INSERT INTO artifacts (event_id, kind, title, uri, created_at)
            VALUES (?, 'markdown', 'Outside', ?, ?)
            """,
            ("evt_delivery", str(outside), "2026-07-24T00:00:00+00:00"),
        )
    bot = SuccessfulBot()
    worker = DeliveryWorker(
        database,
        bot,  # type: ignore[arg-type]
        artifact_allowed_roots=(allowed,),
    )
    await worker.deliver_once()
    assert bot.documents == []


def test_imports_night_agent_outbox(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    payload = {
        "id": "evt_outbox",
        "occurred_at": "2026-07-24T03:12:00+03:00",
        "producer": "night-agent",
        "type": "run_completed",
        "severity": "warning",
        "title": "Imported",
        "requires_action": True,
        "deduplication_key": "night:outbox",
    }
    (outbox / "evt_outbox.json").write_text(json.dumps(payload), encoding="utf-8")
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    worker = DeliveryWorker(
        database,
        SuccessfulBot(),  # type: ignore[arg-type]
        recipients=frozenset({123}),
        night_agent_outbox=outbox,
    )
    assert worker.import_night_agent_outbox() == 1
    assert database.get_event("evt_outbox") is not None
    assert (outbox / "processed" / "evt_outbox.json").exists()


def test_vacancy_event_has_domain_buttons() -> None:
    event = EventCreate.model_validate(
        {
            "id": "evt_vacancy_123",
            "occurred_at": "2026-07-24T03:12:00+03:00",
            "producer": "career-agent",
            "type": "vacancy_found",
            "title": "Vacancy",
            "deduplication_key": "vacancy:123",
            "source": {
                "kind": "vacancy",
                "name": "hh",
                "uri": "https://hh.ru/vacancy/123",
            },
        }
    )
    rows = DeliveryWorker._keyboard_rows(event)
    callback_data = [button.callback_data for row in rows for button in row]
    assert rows[0][0].url == "https://hh.ru/vacancy/123"
    assert "resolve:evt_vacancy_123" in callback_data
