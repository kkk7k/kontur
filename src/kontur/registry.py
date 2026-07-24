from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from kontur.db import Database, utc_now
from kontur.models import AgentView, AutomationView, RegistryStatus


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _next_run(schedule: str | None, timezone: str, now: datetime) -> datetime | None:
    if not schedule or schedule == "external":
        return None
    if schedule.startswith("daily@"):
        hour, minute = map(int, schedule.removeprefix("daily@").split(":"))
        local_now = now.astimezone(ZoneInfo(timezone))
        result = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if result <= local_now:
            result += timedelta(days=1)
        return result
    if schedule.startswith("every:") and schedule.endswith("m"):
        minutes = int(schedule.removeprefix("every:").removesuffix("m"))
        return now + timedelta(minutes=minutes)
    return None


@dataclass(slots=True)
class Registry:
    database: Database
    timezone: str = "Europe/Moscow"

    def agents(self) -> list[AgentView]:
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM agents ORDER BY name").fetchall()
        return [
            AgentView(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                status=RegistryStatus(row["status"]),
                last_run_at=_dt(row["last_run_at"]),
                last_success_at=_dt(row["last_success_at"]),
                last_failure_at=_dt(row["last_failure_at"]),
            )
            for row in rows
        ]

    def automations(self, now: datetime | None = None) -> list[AutomationView]:
        now = now or datetime.now(UTC)
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM automations ORDER BY kind, name"
            ).fetchall()
        return [
            AutomationView(
                id=row["id"],
                name=row["name"],
                agent_id=row["agent_id"],
                kind=row["kind"],
                schedule=row["schedule"],
                status=RegistryStatus(row["status"]),
                last_run_at=_dt(row["last_run_at"]),
                last_success_at=_dt(row["last_success_at"]),
                last_failure_at=_dt(row["last_failure_at"]),
                last_heartbeat_at=_dt(row["last_heartbeat_at"]),
                next_run_at=(
                    _next_run(row["schedule"], self.timezone, now)
                    if row["status"] == "active"
                    else None
                ),
                stale_after_seconds=row["stale_after_seconds"],
            )
            for row in rows
        ]

    def heartbeat(self, automation_id: str, at: datetime | None = None) -> None:
        timestamp = (at or utc_now()).isoformat()
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE automations
                SET last_heartbeat_at = ?, status = 'healthy', updated_at = ?
                WHERE id = ? AND kind = 'service'
                """,
                (timestamp, timestamp, automation_id),
            )

    def stale_services(self, now: datetime | None = None) -> list[AutomationView]:
        now = now or utc_now()
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, COALESCE(last_heartbeat_at, updated_at) AS observed_at,
                       stale_after_seconds
                FROM automations
                WHERE kind = 'service' AND stale_after_seconds IS NOT NULL
                """
            ).fetchall()
        stale_ids = {
            row["id"]
            for row in rows
            if now - datetime.fromisoformat(row["observed_at"])
            > timedelta(seconds=row["stale_after_seconds"])
        }
        return [
            automation
            for automation in self.automations(now)
            if automation.id in stale_ids
        ]

    def open_incident(
        self,
        target_id: str,
        event_id: str,
        at: datetime | None = None,
    ) -> bool:
        timestamp = (at or utc_now()).isoformat()
        with self.database.connection() as connection:
            existing = connection.execute(
                """
                SELECT target_id FROM watchdog_incidents
                WHERE target_id = ? AND resolved_at IS NULL
                """,
                (target_id,),
            ).fetchone()
            if existing:
                return False
            connection.execute(
                """
                INSERT INTO watchdog_incidents (
                    target_id, opened_at, resolved_at, event_id
                ) VALUES (?, ?, NULL, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    opened_at = excluded.opened_at,
                    resolved_at = NULL,
                    event_id = excluded.event_id
                """,
                (target_id, timestamp, event_id),
            )
            connection.execute(
                "UPDATE automations SET status = 'stale', updated_at = ? WHERE id = ?",
                (timestamp, target_id),
            )
            return True

    def close_recovered_incidents(
        self,
        healthy_ids: set[str],
        at: datetime | None = None,
    ) -> list[str]:
        timestamp = (at or utc_now()).isoformat()
        recovered: list[str] = []
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT target_id FROM watchdog_incidents WHERE resolved_at IS NULL"
            ).fetchall()
            for row in rows:
                target_id = row["target_id"]
                if target_id not in healthy_ids:
                    continue
                connection.execute(
                    """
                    UPDATE watchdog_incidents SET resolved_at = ?
                    WHERE target_id = ? AND resolved_at IS NULL
                    """,
                    (timestamp, target_id),
                )
                connection.execute(
                    """
                    UPDATE automations SET status = 'healthy', updated_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, target_id),
                )
                recovered.append(target_id)
        return recovered
