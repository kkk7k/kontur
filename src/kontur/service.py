from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from kontur.db import Database
from kontur.models import (
    AgentView,
    AutomationView,
    EventCreate,
    EventList,
    EventStatus,
    EventView,
    Severity,
    StatusView,
)
from kontur.registry import Registry


@dataclass(slots=True)
class KonturService:
    database: Database
    telegram_recipients: frozenset[int]
    timezone: str = "Europe/Moscow"

    def register_event(self, event: EventCreate) -> tuple[EventView, bool]:
        return self.database.create_event(event, self.telegram_recipients)

    def event(self, event_id: str) -> EventView | None:
        return self.database.get_event(event_id)

    def events(
        self,
        *,
        status: EventStatus | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> EventList:
        items = self.database.list_events(status=status, event_type=event_type, limit=limit)
        return EventList(items=items, total=len(items))

    def transition(self, event_id: str, target: EventStatus, actor_id: str) -> EventView | None:
        return self.database.transition_event(event_id, target, actor_id)

    def status(self) -> StatusView:
        values = self.database.status()
        return StatusView(status="ok", database="ok", **values)

    def agents(self) -> list[AgentView]:
        return Registry(self.database, self.timezone).agents()

    def automations(self) -> list[AutomationView]:
        return Registry(self.database, self.timezone).automations()

    def create_daily_digest(
        self,
        *,
        producer: str,
        now: datetime | None = None,
    ) -> tuple[EventView | None, bool]:
        timezone = ZoneInfo(self.timezone)
        local_now = now.astimezone(timezone) if now else datetime.now(timezone)
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        events = self.database.list_events_since(start)
        if not events:
            return None, False
        actionable = [
            event
            for event in events
            if event.requires_action
            and event.status
            not in {EventStatus.RESOLVED, EventStatus.DISMISSED, EventStatus.SUPPRESSED}
        ]
        errors = [
            event for event in events if event.severity in {Severity.ERROR, Severity.CRITICAL}
        ]
        date_label = local_now.date().isoformat()
        top_items = [
            {
                "id": event.id,
                "type": event.type,
                "severity": event.severity.value,
                "title": event.title,
                "status": event.status.value,
            }
            for event in (actionable or errors or events)[:5]
        ]
        digest = EventCreate(
            id=f"evt_daily_digest_{date_label}",
            occurred_at=local_now,
            producer=producer,
            agent="kontur",
            type="daily_digest_ready",
            severity=Severity.WARNING if errors or actionable else Severity.INFO,
            title=f"Контур: итог за {date_label}",
            summary=(
                f"Событий: {len(events)}. "
                f"Требуют действия: {len(actionable)}. "
                f"Ошибок: {len(errors)}."
            ),
            confidence=1.0,
            requires_action=bool(actionable),
            approval_required=False,
            recommended_action="Открыть /inbox" if actionable else None,
            deduplication_key=f"daily-digest:{date_label}",
            metrics={
                "events_total": len(events),
                "actionable_total": len(actionable),
                "errors_total": len(errors),
            },
            payload={"top_items": top_items, "period_start": start.isoformat()},
            correlation_id=f"digest-{date_label}",
        )
        saved, created = self.database.create_event(digest, self.telegram_recipients)
        self.database.ensure_deliveries(saved.id, self.telegram_recipients)
        return saved, created
