from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from kontur.models import VacancyItem

SENIOR_PATTERN = re.compile(r"\b(senior|sr\.?|старш\w*|ведущ\w*)\b", re.IGNORECASE)


class VacancySource(Protocol):
    name: str

    def fetch(self) -> list[VacancyItem]: ...


def qualifies(title: str, experience_id: str | None) -> list[str]:
    reasons: list[str] = []
    if experience_id == "between3And6":
        reasons.append("опыт 3–6 лет")
    if SENIOR_PATTERN.search(title):
        reasons.append("Senior в названии")
    return reasons


@dataclass(slots=True)
class HHSource:
    search_text: str
    area: str
    user_agent: str
    pages: int = 2
    client_id: str = ""
    client_secret: str = ""
    client: httpx.Client | None = None
    name: str = "hh"

    def fetch(self) -> list[VacancyItem]:
        client = self.client or httpx.Client(timeout=20)
        close_client = self.client is None
        try:
            headers = {"HH-User-Agent": self.user_agent}
            if self.client_id and self.client_secret:
                token_response = client.post(
                    "https://api.hh.ru/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                    },
                    headers=headers,
                )
                token_response.raise_for_status()
                headers["Authorization"] = (
                    f"Bearer {token_response.json()['access_token']}"
                )
            raw_items: dict[str, dict[str, Any]] = {}
            searches = (
                {"text": self.search_text, "experience": "between3And6"},
                {"text": f"({self.search_text}) AND (Senior OR Старший OR Ведущий)"},
            )
            for search in searches:
                for page in range(self.pages):
                    response = client.get(
                        "https://api.hh.ru/vacancies",
                        params={
                            **search,
                            "area": self.area,
                            "order_by": "publication_time",
                            "per_page": 100,
                            "page": page,
                        },
                        headers=headers,
                    )
                    response.raise_for_status()
                    body = response.json()
                    for item in body.get("items", []):
                        raw_items[str(item["id"])] = item
                    if page + 1 >= int(body.get("pages", 0)):
                        break
            return [
                parsed
                for item in raw_items.values()
                if (parsed := self._parse(item)) is not None
            ]
        finally:
            if close_client:
                client.close()

    @staticmethod
    def _parse(item: dict[str, Any]) -> VacancyItem | None:
        experience = item.get("experience") or {}
        reasons = qualifies(item["name"], experience.get("id"))
        if not reasons:
            return None
        salary = item.get("salary") or {}
        employer = item.get("employer") or {}
        return VacancyItem(
            source="hh",
            external_id=str(item["id"]),
            title=item["name"],
            url=item["alternate_url"],
            company=employer.get("name"),
            experience_id=experience.get("id"),
            experience_name=experience.get("name"),
            salary_from=salary.get("from"),
            salary_to=salary.get("to"),
            salary_currency=salary.get("currency"),
            published_at=datetime.fromisoformat(item["published_at"]),
            matched_by=reasons,
        )
