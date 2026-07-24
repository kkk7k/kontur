# n8n для Контура

n8n подключается после запуска Core API и не хранит каноническое состояние.

## Переменные окружения n8n

```dotenv
KONTUR_API_URL=http://host.docker.internal:8090
KONTUR_N8N_API_KEY=change-me
KONTUR_N8N_WEBHOOK_SECRET=change-me-too
```

В `KONTUR_API_KEYS` Core должен существовать соответствующий producer:

```dotenv
KONTUR_API_KEYS=night-agent:secret,n8n:change-me
```

Автоматическая локальная настройка без вывода secret:

```bash
kontur-configure-n8n
```

Команда сохраняет существующие producer keys, добавляет или ротирует только
ключ `n8n`, атомарно обновляет локальные `.env` и выставляет права `0600`.

## Workflow

- `kontur_agent_webhook.json` — принимает уже сформированный контракт события,
  проверяет shared secret и регистрирует событие в Core.
- `kontur_daily_digest.json` — каждый день в 08:30 МСК просит Core создать
  агрегированный итог; при отсутствии событий Core возвращает `204`.
- `kontur_status_watchdog.json` — проверяет состояние Core каждые 15 минут.

JSON-файлы не содержат credentials. После импорта проверить URL и переменные
окружения в конкретной установке n8n.

## Локальное развёртывание

На 2026-07-24 `kontur_daily_digest` импортирован в существующий локальный n8n:

- URL: `http://localhost:5678`;
- workflow ID: `0BzScgOaEFOuFZqn`;
- состояние: active;
- отдельный producer key настроен и хранится только в локальных `.env`;
- расписание: ежедневно в 08:30 `Europe/Moscow`;
- Manual Trigger оставлен для безопасной CLI-проверки после обновлений;
- end-to-end CLI execution пройден 2026-07-24.

Нельзя публиковать webhook в интернет без TLS, дополнительной авторизации и
ограничения доступа.
