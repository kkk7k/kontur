from __future__ import annotations

import json

from kontur.config import Settings
from kontur.db import Database
from kontur.service import KonturService
from kontur.vacancies import HHSource
from kontur.vacancy_collector import VacancyCollector


def main() -> None:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    database.initialize()
    service = KonturService(
        database,
        settings.telegram_allowed_user_ids,
        timezone=settings.timezone,
        vacancy_recipients=settings.telegram_vacancy_user_ids,
    )
    result = VacancyCollector(database, service).run(
        HHSource(
            search_text=settings.hh_search_text,
            area=settings.hh_area,
            user_agent=settings.hh_user_agent,
            pages=settings.hh_pages,
            client_id=settings.hh_client_id,
            client_secret=settings.hh_client_secret,
        )
    )
    print(json.dumps(result.model_dump(), ensure_ascii=False))


if __name__ == "__main__":
    main()
