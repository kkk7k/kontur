from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bot_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    user_id INTEGER,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS delivery_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    recipient TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_bot_interactions_occurred_at
ON bot_interactions(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_interactions_name ON bot_interactions(name);
CREATE INDEX IF NOT EXISTS idx_delivery_outcomes_occurred_at
ON delivery_outcomes(occurred_at DESC);
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class AnalyticsStore:
    """Separate, best-effort store for product-usage metrics and delivery
    logs. Kept out of the main operational database on purpose: this is
    write-heavy, disposable telemetry meant for iterating on the bot's UX
    (which commands get used, how slow they are, what breaks), not
    something the rest of the system depends on for correctness.
    """

    path: Path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record_interaction(
        self,
        *,
        user_id: int | None,
        kind: str,
        name: str,
        duration_ms: float,
        ok: bool,
        error: str | None = None,
        at: datetime | None = None,
    ) -> None:
        try:
            with self.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO bot_interactions (
                        occurred_at, user_id, kind, name, duration_ms, ok, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (at or _utc_now()).isoformat(),
                        user_id,
                        kind,
                        name,
                        duration_ms,
                        1 if ok else 0,
                        error,
                    ),
                )
        except Exception:
            LOGGER.warning("analytics: failed to record interaction", exc_info=True)

    def record_delivery(
        self,
        *,
        event_type: str,
        recipient: str,
        attempt: int,
        ok: bool,
        error: str | None = None,
        at: datetime | None = None,
    ) -> None:
        try:
            with self.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO delivery_outcomes (
                        occurred_at, event_type, recipient, attempt, ok, error
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (at or _utc_now()).isoformat(),
                        event_type,
                        recipient,
                        attempt,
                        1 if ok else 0,
                        error,
                    ),
                )
        except Exception:
            LOGGER.warning("analytics: failed to record delivery outcome", exc_info=True)
