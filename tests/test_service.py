from pathlib import Path

from kontur.db import Database
from kontur.models import EventCreate
from kontur.service import KonturService


def test_status_counts_actionable_events(tmp_path: Path) -> None:
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    service = KonturService(database, frozenset())
    service.register_event(
        EventCreate.model_validate(
            {
                "id": "evt_action",
                "occurred_at": "2026-07-24T03:12:00+03:00",
                "producer": "test",
                "type": "decision_required",
                "title": "Choose",
                "requires_action": True,
                "deduplication_key": "test:action",
            }
        )
    )
    assert service.status().pending_inbox == 1
