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

## Workflow

- `kontur_agent_webhook.json` — принимает уже сформированный контракт события,
  проверяет shared secret и регистрирует событие в Core.
- `kontur_status_watchdog.json` — проверяет состояние Core каждые 15 минут.

JSON-файлы не содержат credentials. После импорта проверить URL и переменные
окружения в конкретной установке n8n.

Нельзя публиковать webhook в интернет без TLS, дополнительной авторизации и
ограничения доступа.
