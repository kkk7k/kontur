from __future__ import annotations

from dataclasses import dataclass

from kontur.db import Database
from kontur.models import EventCreate, EventList, EventStatus, EventView, StatusView


@dataclass(slots=True)
class KonturService:
    database: Database
    telegram_recipients: frozenset[int]

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
