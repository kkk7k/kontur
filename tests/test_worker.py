from pathlib import Path
from types import SimpleNamespace

import pytest

from kontur.db import Database
from kontur.models import EventCreate, EventStatus
from kontur.worker import DeliveryWorker


class SuccessfulBot:
    async def send_message(self, **_: object) -> SimpleNamespace:
        return SimpleNamespace(message_id=42)


class FailingBot:
    async def send_message(self, **_: object) -> None:
        raise ConnectionError("secret details must not be stored")


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
