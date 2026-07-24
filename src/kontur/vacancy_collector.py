from __future__ import annotations

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
            event_id = f"evt_vacancy_{item.source}_{item.external_id}"
            event = EventCreate(
                id=event_id,
                occurred_at=item.published_at,
                producer=f"vacancy-{item.source}",
                agent="career",
                type="vacancy_found",
                severity=Severity.INFO,
                title=item.title,
                summary=(
                    f"{item.company or 'Компания не указана'}. "
                    f"{item.experience_name or 'Опыт не указан'}. {_salary(item)}. "
                    f"Фильтр: {', '.join(item.matched_by)}."
                ),
                requires_action=True,
                recommended_action="Открыть вакансию",
                deduplication_key=f"{item.source}:{item.external_id}",
                source={"kind": "vacancy", "name": item.source, "uri": item.url},
                payload=item.model_dump(mode="json"),
                correlation_id=f"{item.source}:{item.external_id}",
            )
            if bootstrapped:
                saved, event_created = self.database.create_event(event, frozenset())
            else:
                saved, event_created = self.service.register_event(event)
            stored = self.database.create_vacancy_item(vacancy=item, event_id=saved.id)
            if event_created and stored:
                created += 1
        return VacancyCollectionResult(
            source=source.name,
            fetched=len(items),
            qualified=len(items),
            created=created,
            duplicate=len(items) - created,
            bootstrapped=bootstrapped,
        )
