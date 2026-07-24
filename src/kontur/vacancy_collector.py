from __future__ import annotations

import hashlib
from dataclasses import dataclass

from kontur.db import Database
from kontur.models import EventCreate, Severity, VacancyCollectionResult, VacancyItem
from kontur.service import KonturService
from kontur.vacancies import VacancySource


def _salary(item: VacancyItem) -> str:
    if item.salary_from is None and item.salary_to is None:
        return "зарплата не указана"
    bounds = "–".join(
        str(value) for value in (item.salary_from, item.salary_to) if value is not None
    )
    return f"{bounds} {item.salary_currency or ''}".strip()


@dataclass(slots=True)
class VacancyCollector:
    database: Database
    service: KonturService

    def run(self, source: VacancySource) -> VacancyCollectionResult:
        bootstrapped = self.database.vacancy_item_count(source.name) == 0
        items = source.fetch()
        created = 0
        for item in items:
            if self.ingest(item, notify=not bootstrapped):
                created += 1
        return VacancyCollectionResult(
            source=source.name,
            fetched=len(items),
            qualified=len(items),
            created=created,
            duplicate=len(items) - created,
            bootstrapped=bootstrapped,
        )

    def ingest(self, item: VacancyItem, *, notify: bool = True) -> bool:
        digest = hashlib.sha256(
            f"{item.source}:{item.external_id}".encode()
        ).hexdigest()[:20]
        event = EventCreate(
            id=f"evt_vacancy_{digest}",
            occurred_at=item.published_at,
            producer=f"vacancy-{item.source}",
            agent="career",
            type="vacancy_found",
            severity=Severity.INFO,
            title=item.title,
            summary=(
                f"{item.company or 'Компания не указана'}. "
                f"{item.experience_name or 'Опыт не указан'}. {_salary(item)}. "
                f"Фильтр: {', '.join(item.matched_by) or 'внешний источник'}."
            ),
            requires_action=True,
            recommended_action="Открыть вакансию",
            deduplication_key=f"{item.source}:{item.external_id}",
            source={"kind": "vacancy", "name": item.source, "uri": item.url},
            payload=item.model_dump(mode="json"),
            correlation_id=f"{item.source}:{item.external_id}"[:100],
        )
        if notify:
            saved, event_created = self.service.register_event(event)
        else:
            saved, event_created = self.database.create_event(event, frozenset())
        stored = self.database.create_vacancy_item(vacancy=item, event_id=saved.id)
        return event_created and stored
