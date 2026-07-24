from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from kontur.db import Database
from kontur.models import EventCreate, RegistryStatus
from kontur.registry import Registry
from kontur.service import KonturService
from kontur.watchdog import Watchdog


def setup(tmp_path: Path) -> tuple[Database, KonturService, Registry]:
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    service = KonturService(database, frozenset({42}))
    return database, service, Registry(database)


def test_registry_is_seeded_and_daily_next_run_is_calculated(tmp_path: Path) -> None:
    _, _, registry = setup(tmp_path)
    now = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)

    assert {agent.id for agent in registry.agents()} >= {
        "night-agent",
        "career",
        "travel",
        "renovation",
        "reputation",
    }
    digest = next(
        item for item in registry.automations(now) if item.id == "n8n-daily-digest"
    )
    assert digest.status is RegistryStatus.ACTIVE
    assert digest.next_run_at == datetime(
        2026, 7, 25, 8, 30, tzinfo=ZoneInfo("Europe/Moscow")
    )


def test_night_event_updates_agent_and_automation(tmp_path: Path) -> None:
    _, service, _ = setup(tmp_path)
    occurred_at = datetime(2026, 7, 24, 3, 0, tzinfo=UTC)
    service.register_event(
        EventCreate(
            id="evt_night_registry",
            occurred_at=occurred_at,
            producer="night-agent",
            agent="coding",
            type="run_completed",
            title="Night done",
            deduplication_key="night:registry",
        )
    )

    agent = next(item for item in service.agents() if item.id == "night-agent")
    automation = next(
        item
        for item in service.automations()
        if item.id == "night-agent-nightly"
    )
    assert agent.last_success_at == occurred_at
    assert automation.last_success_at == occurred_at


def test_initialize_backfills_registry_from_existing_events(tmp_path: Path) -> None:
    database, service, _ = setup(tmp_path)
    occurred_at = datetime(2026, 7, 23, 5, 30, tzinfo=UTC)
    service.register_event(
        EventCreate(
            id="evt_digest_history",
            occurred_at=occurred_at,
            producer="n8n",
            type="daily_digest_ready",
            title="Digest",
            deduplication_key="digest:history",
        )
    )
    with database.connection() as connection:
        connection.execute(
            """
            UPDATE automations
            SET last_run_at = NULL, last_success_at = NULL
            WHERE id = 'n8n-daily-digest'
            """
        )

    database.initialize()

    digest = next(
        item
        for item in Registry(database).automations()
        if item.id == "n8n-daily-digest"
    )
    assert digest.last_success_at == occurred_at


def test_watchdog_opens_one_incident_until_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database, service, registry = setup(tmp_path)
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    old = (now - timedelta(minutes=10)).isoformat()
    with database.connection() as connection:
        connection.execute(
            """
            UPDATE automations SET last_heartbeat_at = ?, updated_at = ?
            WHERE id IN ('kontur-api', 'kontur-bot')
            """,
            (old, old),
        )

    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(
            200,
            request=httpx.Request("GET", "http://n8n/healthz"),
        ),
    )
    watchdog = Watchdog(registry, service)

    assert watchdog.check(now) == 2
    assert watchdog.check(now + timedelta(seconds=30)) == 0
    assert service.events(event_type="automation_failed").total == 2

    registry.heartbeat("kontur-api", now + timedelta(seconds=40))
    registry.heartbeat("kontur-bot", now + timedelta(seconds=40))
    assert watchdog.check(now + timedelta(seconds=41)) == 0
    with database.connection() as connection:
        unresolved = connection.execute(
            "SELECT COUNT(*) FROM watchdog_incidents WHERE resolved_at IS NULL"
        ).fetchone()[0]
    assert unresolved == 0
