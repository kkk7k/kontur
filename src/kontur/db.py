from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from kontur.models import (
    EventCreate,
    EventStatus,
    EventView,
    ReputationSignalCreate,
    VacancyItem,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    producer TEXT NOT NULL,
    agent TEXT,
    run_id TEXT,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL,
    requires_action INTEGER NOT NULL,
    approval_required INTEGER NOT NULL,
    recommended_action TEXT,
    deduplication_key TEXT NOT NULL,
    source_json TEXT,
    metrics_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (producer, deduplication_key)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    uri TEXT NOT NULL,
    content_type TEXT,
    checksum TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    external_message_id TEXT,
    last_error_safe TEXT,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (event_id, channel, recipient)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    result TEXT NOT NULL,
    metadata_safe_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancy_items (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE REFERENCES events(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    company TEXT,
    experience_id TEXT,
    experience_name TEXT,
    salary_from INTEGER,
    salary_to INTEGER,
    salary_currency TEXT,
    published_at TEXT NOT NULL,
    matched_by_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    last_run_at TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_id TEXT REFERENCES agents(id),
    kind TEXT NOT NULL,
    schedule TEXT,
    status TEXT NOT NULL,
    last_run_at TEXT,
    last_success_at TEXT,
    last_failure_at TEXT,
    last_heartbeat_at TEXT,
    stale_after_seconds INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchdog_incidents (
    target_id TEXT PRIMARY KEY REFERENCES automations(id),
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    event_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reputation_checkpoints (
    repository TEXT PRIMARY KEY,
    commit_sha TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reputation_signals (
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    audience TEXT,
    observed_at TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    frequency INTEGER NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    PRIMARY KEY (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_deliveries_pending
ON deliveries(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_vacancy_items_published
ON vacancy_items(published_at DESC);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._seed_registry(connection)
            self._backfill_registry(connection)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_event(
        self,
        event: EventCreate,
        recipients: frozenset[int],
    ) -> tuple[EventView, bool]:
        now = utc_now()
        now_text = now.isoformat()
        with self.connection() as connection:
            existing = connection.execute(
                """
                SELECT * FROM events
                WHERE id = ?
                OR (producer = ? AND deduplication_key = ?)
                LIMIT 1
                """,
                (event.id, event.producer, event.deduplication_key),
            ).fetchone()
            if existing:
                return self._row_to_event(connection, existing), False

            data = event.model_dump(mode="json", exclude={"artifacts"})
            connection.execute(
                """
                INSERT INTO events (
                    id, schema_version, occurred_at, received_at, producer, agent,
                    run_id, type, severity, title, summary, status, confidence,
                    requires_action, approval_required, recommended_action,
                    deduplication_key, source_json, metrics_json, payload_json,
                    correlation_id, created_at, updated_at
                ) VALUES (
                    :id, :schema_version, :occurred_at, :received_at, :producer, :agent,
                    :run_id, :type, :severity, :title, :summary, :status, :confidence,
                    :requires_action, :approval_required, :recommended_action,
                    :deduplication_key, :source_json, :metrics_json, :payload_json,
                    :correlation_id, :created_at, :updated_at
                )
                """,
                {
                    **data,
                    "received_at": now_text,
                    "status": EventStatus.NEW.value,
                    "requires_action": int(event.requires_action),
                    "approval_required": int(event.approval_required),
                    "source_json": json.dumps(data["source"], ensure_ascii=False)
                    if data["source"]
                    else None,
                    "metrics_json": json.dumps(data["metrics"], ensure_ascii=False),
                    "payload_json": json.dumps(data["payload"], ensure_ascii=False),
                    "created_at": now_text,
                    "updated_at": now_text,
                },
            )
            for artifact in event.artifacts:
                connection.execute(
                    """
                    INSERT INTO artifacts (
                        event_id, kind, title, uri, content_type, checksum, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        artifact.kind,
                        artifact.title,
                        artifact.uri,
                        artifact.content_type,
                        artifact.checksum,
                        now_text,
                    ),
                )
            if self._should_notify(event):
                for recipient in recipients:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO deliveries (
                            event_id, channel, recipient, status, attempts,
                            next_attempt_at, created_at, updated_at
                        ) VALUES (?, 'telegram', ?, 'pending', 0, ?, ?, ?)
                        """,
                        (event.id, str(recipient), now_text, now_text, now_text),
                    )
            self._audit(
                connection,
                actor_type="producer",
                actor_id=event.producer,
                action="event.created",
                entity_type="event",
                entity_id=event.id,
                result="success",
            )
            self._update_registry_from_event(connection, event)
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event.id,)).fetchone()
            assert row is not None
            return self._row_to_event(connection, row), True

    @staticmethod
    def _seed_registry(connection: sqlite3.Connection) -> None:
        now = utc_now().isoformat()
        agents = (
            ("night-agent", "Night Agent", "Ночные технические задачи", "active"),
            ("career", "Career Agent", "Карьера и вакансии", "active"),
            ("travel", "Travel Deal Agent", "Выгодные поездки", "planned"),
            ("renovation", "Renovation Price Agent", "Закупки для ремонта", "planned"),
            (
                "reputation",
                "Reputation Research Agent",
                "Доказательные темы и исследование спроса",
                "active",
            ),
        )
        for values in agents:
            connection.execute(
                """
                INSERT OR IGNORE INTO agents (
                    id, name, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (*values, now, now),
            )
        automations = (
            (
                "kontur-api", "Kontur Core API", None, "service", None,
                "healthy", 90,
            ),
            (
                "kontur-bot", "Telegram Bot", None, "service", None,
                "healthy", 90,
            ),
            (
                "kontur-worker", "Delivery Worker + Watchdog", None, "service",
                None, "healthy", 90,
            ),
            (
                "n8n", "n8n", None, "service", None, "unknown", 180,
            ),
            (
                "night-agent-nightly", "Night Agent nightly", "night-agent",
                "workflow", "external", "active", None,
            ),
            (
                "n8n-daily-digest", "Daily digest", None, "workflow",
                "daily@08:30", "active", None,
            ),
            (
                "hh-vacancy-monitor", "HH vacancy monitor", "career", "workflow",
                "every:30m", "paused", None,
            ),
            (
                "reputation-weekly",
                "Reputation weekly research",
                "reputation",
                "workflow",
                "weekly@sun:19:00",
                "active",
                None,
            ),
        )
        for automation in automations:
            connection.execute(
                """
                INSERT OR IGNORE INTO automations (
                    id, name, agent_id, kind, schedule, status,
                    stale_after_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*automation, now, now),
            )

    @staticmethod
    def _update_registry_from_event(
        connection: sqlite3.Connection,
        event: EventCreate,
    ) -> None:
        occurred = event.occurred_at.isoformat()
        success = event.type in {
            "run_completed",
            "daily_digest_ready",
            "reputation_weekly_digest",
        }
        failure = event.type in {"run_failed"}
        agent_id = "night-agent" if event.producer == "night-agent" else event.agent
        if agent_id:
            connection.execute(
                """
                UPDATE agents
                SET last_run_at = ?,
                    last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                    last_failure_at = CASE WHEN ? THEN ? ELSE last_failure_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    occurred, int(success), occurred, int(failure), occurred,
                    utc_now().isoformat(), agent_id,
                ),
            )
        automation_id = event.payload.get("automation_id")
        if not automation_id and event.type == "daily_digest_ready":
            automation_id = "n8n-daily-digest"
        if not automation_id and event.producer == "night-agent":
            automation_id = "night-agent-nightly"
        if automation_id:
            connection.execute(
                """
                UPDATE automations
                SET last_run_at = ?,
                    last_success_at = CASE WHEN ? THEN ? ELSE last_success_at END,
                    last_failure_at = CASE WHEN ? THEN ? ELSE last_failure_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    occurred, int(success), occurred, int(failure), occurred,
                    utc_now().isoformat(), str(automation_id),
                ),
            )

    @staticmethod
    def _backfill_registry(connection: sqlite3.Connection) -> None:
        mappings = (
            ("agents", "night-agent", "producer = 'night-agent'"),
            ("agents", "career", "agent = 'career'"),
            ("automations", "night-agent-nightly", "producer = 'night-agent'"),
            ("automations", "n8n-daily-digest", "type = 'daily_digest_ready'"),
            (
                "agents",
                "reputation",
                "type = 'reputation_weekly_digest'",
            ),
            (
                "automations",
                "reputation-weekly",
                "type = 'reputation_weekly_digest'",
            ),
        )
        for table, entity_id, condition in mappings:
            row = connection.execute(
                f"""
                SELECT
                    MAX(occurred_at) AS last_run_at,
                    MAX(CASE
                        WHEN type IN ('run_completed', 'daily_digest_ready')
                        THEN occurred_at
                    END) AS last_success_at,
                    MAX(CASE WHEN type = 'run_failed' THEN occurred_at END)
                        AS last_failure_at
                FROM events WHERE {condition}
                """  # noqa: S608
            ).fetchone()
            if not row or not row["last_run_at"]:
                continue
            connection.execute(
                f"""
                UPDATE {table}
                SET last_run_at = MAX(
                        COALESCE(last_run_at, ''), COALESCE(?, '')
                    ),
                    last_success_at = NULLIF(MAX(
                        COALESCE(last_success_at, ''), COALESCE(?, '')
                    ), ''),
                    last_failure_at = NULLIF(MAX(
                        COALESCE(last_failure_at, ''), COALESCE(?, '')
                    ), ''),
                    updated_at = ?
                WHERE id = ?
                """,  # noqa: S608
                (
                    row["last_run_at"],
                    row["last_success_at"],
                    row["last_failure_at"],
                    utc_now().isoformat(),
                    entity_id,
                ),
            )
    def ensure_deliveries(self, event_id: str, recipients: frozenset[int]) -> None:
        now_text = utc_now().isoformat()
        with self.connection() as connection:
            for recipient in recipients:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO deliveries (
                        event_id, channel, recipient, status, attempts,
                        next_attempt_at, created_at, updated_at
                    ) VALUES (?, 'telegram', ?, 'pending', 0, ?, ?, ?)
                    """,
                    (event_id, str(recipient), now_text, now_text, now_text),
                )

    def create_vacancy_item(
        self,
        *,
        vacancy: VacancyItem,
        event_id: str,
    ) -> bool:
        now = utc_now().isoformat()
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO vacancy_items (
                    source, external_id, event_id, title, url, company,
                    experience_id, experience_name, salary_from, salary_to,
                    salary_currency, published_at, matched_by_json, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vacancy.source,
                    vacancy.external_id,
                    event_id,
                    vacancy.title,
                    vacancy.url,
                    vacancy.company,
                    vacancy.experience_id,
                    vacancy.experience_name,
                    vacancy.salary_from,
                    vacancy.salary_to,
                    vacancy.salary_currency,
                    vacancy.published_at.isoformat(),
                    json.dumps(vacancy.matched_by, ensure_ascii=False),
                    now,
                ),
            )
            return cursor.rowcount == 1

    def vacancy_item_count(self, source: str) -> int:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM vacancy_items WHERE source = ?",
                (source,),
            ).fetchone()
            return int(row["count"])

    def reputation_checkpoint(self, repository: str) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT commit_sha FROM reputation_checkpoints WHERE repository = ?",
                (repository,),
            ).fetchone()
        return str(row["commit_sha"]) if row else None

    def save_reputation_checkpoint(self, repository: str, commit_sha: str) -> None:
        now = utc_now().isoformat()
        with self.connection() as connection:
            connection.execute(
                """
                INSERT INTO reputation_checkpoints (repository, commit_sha, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(repository) DO UPDATE SET
                    commit_sha = excluded.commit_sha,
                    updated_at = excluded.updated_at
                """,
                (repository, commit_sha, now),
            )

    def create_reputation_signal(self, signal: ReputationSignalCreate) -> bool:
        now = utc_now().isoformat()
        with self.connection() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO reputation_signals (
                    source, external_id, url, title, summary, audience,
                    observed_at, tags_json, frequency, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.source,
                    signal.external_id,
                    signal.url,
                    signal.title,
                    signal.summary,
                    signal.audience,
                    signal.observed_at.isoformat(),
                    json.dumps(signal.tags, ensure_ascii=False),
                    signal.frequency,
                    now,
                ),
            )
            return cursor.rowcount == 1

    def pending_reputation_signals(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT * FROM reputation_signals
                WHERE processed_at IS NULL
                ORDER BY frequency DESC, observed_at DESC
                LIMIT 500
                """
            ).fetchall()

    def mark_reputation_signals_processed(self) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE reputation_signals SET processed_at = ?
                WHERE processed_at IS NULL
                """,
                (utc_now().isoformat(),),
            )

    def get_event(self, event_id: str) -> EventView | None:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return self._row_to_event(connection, row) if row else None

    def list_events(
        self,
        *,
        status: EventStatus | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[EventView]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM events {where} ORDER BY occurred_at DESC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
            return [self._row_to_event(connection, row) for row in rows]

    def count_events(
        self,
        *,
        status: EventStatus | None = None,
        event_type: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if event_type:
            clauses.append("type = ?")
            params.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) AS total FROM events {where}",  # noqa: S608
                params,
            ).fetchone()
            return int(row["total"])

    def list_events_since(
        self,
        since: datetime,
        *,
        limit: int = 500,
    ) -> list[EventView]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE datetime(occurred_at) >= datetime(?)
                AND type != 'daily_digest_ready'
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (since.isoformat(), limit),
            ).fetchall()
            return [self._row_to_event(connection, row) for row in rows]

    def transition_event(
        self,
        event_id: str,
        target: EventStatus,
        actor_id: str,
    ) -> EventView | None:
        allowed = {
            EventStatus.NEW: {EventStatus.NOTIFIED, EventStatus.SEEN, EventStatus.RESOLVED},
            EventStatus.NOTIFIED: {EventStatus.SEEN, EventStatus.RESOLVED, EventStatus.DISMISSED},
            EventStatus.SEEN: {EventStatus.RESOLVED, EventStatus.DISMISSED},
            EventStatus.DELIVERY_FAILED: {EventStatus.NOTIFIED, EventStatus.RESOLVED},
        }
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if not row:
                return None
            current = EventStatus(row["status"])
            if target == current:
                return self._row_to_event(connection, row)
            if target not in allowed.get(current, set()):
                raise ValueError(f"invalid event transition: {current} -> {target}")
            connection.execute(
                "UPDATE events SET status = ?, updated_at = ? WHERE id = ?",
                (target.value, utc_now().isoformat(), event_id),
            )
            self._audit(
                connection,
                actor_type="user",
                actor_id=actor_id,
                action=f"event.{target.value}",
                entity_type="event",
                entity_id=event_id,
                result="success",
            )
            updated = connection.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_event(connection, updated)

    def status(self) -> dict[str, object]:
        with self.connection() as connection:
            pending = connection.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE requires_action = 1
                AND status IN ('new', 'notified', 'seen', 'delivery_failed')
                """
            ).fetchone()[0]
            failed = connection.execute(
                "SELECT COUNT(*) FROM deliveries WHERE status = 'failed'"
            ).fetchone()[0]
            latest = connection.execute(
                "SELECT occurred_at FROM events ORDER BY occurred_at DESC LIMIT 1"
            ).fetchone()
            return {
                "pending_inbox": pending,
                "failed_deliveries": failed,
                "latest_event_at": datetime.fromisoformat(latest[0]) if latest else None,
            }

    @staticmethod
    def _should_notify(event: EventCreate) -> bool:
        return event.severity.value in {"error", "critical"} or event.requires_action

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        result: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log (
                occurred_at, actor_type, actor_id, action,
                entity_type, entity_id, result, metadata_safe_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '{}')
            """,
            (
                utc_now().isoformat(),
                actor_type,
                actor_id,
                action,
                entity_type,
                entity_id,
                result,
            ),
        )

    @staticmethod
    def _row_to_event(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> EventView:
        artifacts = connection.execute(
            """
            SELECT kind, title, uri, content_type, checksum
            FROM artifacts WHERE event_id = ? ORDER BY id
            """,
            (row["id"],),
        ).fetchall()
        return EventView.model_validate(
            {
                "id": row["id"],
                "schema_version": row["schema_version"],
                "occurred_at": row["occurred_at"],
                "received_at": row["received_at"],
                "producer": row["producer"],
                "agent": row["agent"],
                "run_id": row["run_id"],
                "type": row["type"],
                "severity": row["severity"],
                "title": row["title"],
                "summary": row["summary"],
                "status": row["status"],
                "confidence": row["confidence"],
                "requires_action": bool(row["requires_action"]),
                "approval_required": bool(row["approval_required"]),
                "recommended_action": row["recommended_action"],
                "deduplication_key": row["deduplication_key"],
                "source": json.loads(row["source_json"]) if row["source_json"] else None,
                "metrics": json.loads(row["metrics_json"]),
                "payload": json.loads(row["payload_json"]),
                "artifacts": [dict(artifact) for artifact in artifacts],
                "correlation_id": row["correlation_id"],
            }
        )
