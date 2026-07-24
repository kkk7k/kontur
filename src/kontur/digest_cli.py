from __future__ import annotations

from kontur.config import Settings
from kontur.db import Database
from kontur.service import KonturService


def main() -> int:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    database.initialize()
    service = KonturService(
        database,
        settings.telegram_allowed_user_ids,
        timezone=settings.timezone,
        vacancy_recipients=settings.telegram_vacancy_user_ids,
    )
    event, created = service.create_daily_digest(producer="kontur-cli")
    if event is None:
        print("No events for today's digest.")
        return 0
    print(f"{event.id} ({'created' if created else 'already exists'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
