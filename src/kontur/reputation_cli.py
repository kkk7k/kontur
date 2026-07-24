from __future__ import annotations

import json

from kontur.config import Settings
from kontur.db import Database
from kontur.reputation import ReputationResearchAgent
from kontur.service import KonturService


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
    result = ReputationResearchAgent(
        database=database,
        service=service,
        repositories=settings.reputation_repositories,
        timezone=settings.timezone,
        author_filters=settings.reputation_git_authors,
    ).run()
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


if __name__ == "__main__":
    main()
