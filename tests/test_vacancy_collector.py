from datetime import UTC, datetime
from pathlib import Path

import httpx

from kontur.db import Database
from kontur.service import KonturService
from kontur.vacancies import HHSource, qualifies
from kontur.vacancy_collector import VacancyCollector


def hh_item(
    vacancy_id: str,
    *,
    title: str = "Go-разработчик",
    experience_id: str = "between3And6",
) -> dict:
    return {
        "id": vacancy_id,
        "name": title,
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "employer": {"name": "Example"},
        "experience": {"id": experience_id, "name": "От 3 до 6 лет"},
        "salary": {"from": 300000, "to": None, "currency": "RUR"},
        "published_at": datetime(2026, 7, 24, 9, tzinfo=UTC).isoformat(),
    }


def test_filter_accepts_experience_or_senior() -> None:
    assert qualifies("Go-разработчик", "between3And6") == ["опыт 3–6 лет"]
    assert qualifies("Senior Go Developer", "between1And3") == ["Senior в названии"]
    assert qualifies("Middle Go Developer", "between1And3") == []


def test_hh_source_merges_searches_and_filters() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        items = (
            [hh_item("1"), hh_item("2", title="Middle Go", experience_id="between1And3")]
            if calls == 1
            else [hh_item("1"), hh_item("3", title="Senior Go", experience_id="between1And3")]
        )
        return httpx.Response(200, json={"items": items, "pages": 1})

    source = HHSource(
        search_text="Go",
        area="113",
        user_agent="test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    items = source.fetch()
    assert {item.external_id for item in items} == {"1", "3"}
    assert calls == 2


def test_collector_creates_only_new_events(tmp_path: Path) -> None:
    database = Database(tmp_path / "kontur.sqlite")
    database.initialize()
    service = KonturService(database, frozenset({42}))
    item = HHSource._parse(hh_item("123"))
    assert item is not None

    class Source:
        name = "hh"

        def fetch(self):
            return [item]

    collector = VacancyCollector(database, service)
    first = collector.run(Source())
    second = collector.run(Source())

    assert first.created == 1
    assert first.bootstrapped is True
    assert second.created == 0
    assert second.bootstrapped is False
    assert second.duplicate == 1
    events = service.events(event_type="vacancy_found")
    assert events.total == 1
    assert events.items[0].source.uri == "https://hh.ru/vacancy/123"
