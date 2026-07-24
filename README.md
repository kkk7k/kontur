# Контур

Личный центр событий, запусков и решений AI-агентов.

Telegram-интерфейс: [@personal_kontur_bot](https://t.me/personal_kontur_bot).

## MVP

Первый вертикальный сценарий:

```text
Night Agent -> Kontur API -> SQLite -> delivery worker -> Telegram
```

Монитор вакансий:

```text
HH / Telegram / Habr / company sites -> n8n -> Kontur -> dedup -> Telegram
```

Вакансия считается подходящей для
уведомления, если у неё указан опыт `3–6 лет` или в названии есть
`Senior`, `Старший` либо `Ведущий`. Повторно одна и та же вакансия не
отправляется.

Адаптер официального API HH готов, но его n8n workflow выключен: анонимный
поиск из локального окружения получает `403`. Код сохранён для будущего
подключения через OAuth-приложение или email сохранённого поиска HH.

Остальные источники могут сразу передавать нормализованные вакансии в
`POST /api/v1/vacancies`.

Контур хранит события независимо от Telegram. Повторная регистрация события
идемпотентна, а неудачная доставка остаётся в outbox и повторяется.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Заполнить в `.env`:

- `KONTUR_API_KEYS`;
- `KONTUR_TELEGRAM_BOT_TOKEN`;
- `KONTUR_TELEGRAM_ALLOWED_USER_IDS`.

Если numeric user ID ещё неизвестен, разрешён одноразовый режим:

```dotenv
KONTUR_TELEGRAM_ALLOWED_USER_IDS=
KONTUR_TELEGRAM_DISCOVERY_MODE=true
```

В этом режиме обычное сообщение боту возвращает ID отправителя, но не открывает
доступ к событиям. После обнаружения ID режим необходимо отключить.

Запуск компонентов в отдельных терминалах:

```bash
kontur-api
kontur-worker
kontur-bot
```

Ручное формирование ежедневного digest:

```bash
kontur-digest
```

Ручной запуск поиска вакансий (обычно этот endpoint вызывает n8n):

```bash
curl -X POST http://127.0.0.1:8090/api/v1/collectors/hh/run \
  -H 'X-Kontur-Producer: n8n' \
  -H 'X-Kontur-API-Key: change-me'
```

Регистрация вакансии из любого внешнего источника:

```bash
curl -X POST http://127.0.0.1:8090/api/v1/vacancies \
  -H 'Content-Type: application/json' \
  -H 'X-Kontur-Producer: n8n' \
  -H 'X-Kontur-API-Key: change-me' \
  -d '{
    "source": "telegram",
    "external_id": "channel-name:123",
    "title": "Senior Go Developer",
    "url": "https://t.me/channel-name/123",
    "company": "Example",
    "published_at": "2026-07-24T12:00:00+03:00",
    "matched_by": ["Senior в названии"]
  }'
```

То же действие доступно владельцу через `/digest` в Telegram.

Проверка:

```bash
curl http://127.0.0.1:8090/health/live
pytest
```

## Регистрация события

```bash
curl \
  -X POST http://127.0.0.1:8090/api/v1/events \
  -H 'Content-Type: application/json' \
  -H 'X-Kontur-Producer: night-agent' \
  -H 'X-Kontur-API-Key: change-me' \
  -H 'Idempotency-Key: night-agent:2026-07-24:completed' \
  -d @examples/run_completed.json
```

## Адаптер Night Agent

После формирования `morning-report.md`:

```bash
export KONTUR_NIGHT_AGENT_API_KEY=change-me
kontur-night-report \
  /absolute/path/to/runs/2026-07-24/morning-report.md \
  --exit-code 0
```

Если Core временно недоступен, событие атомарно сохраняется в
`runtime/night-agent-outbox`. Worker автоматически импортирует такие события,
перемещает исходный JSON в `runtime/night-agent-outbox/processed` и ставит
уведомление в очередь. Недоступность Telegram или Контура не меняет результат
самой ночной технической работы.

Если событие содержит локальный markdown-артефакт, worker отправляет его
отдельным Telegram-документом. Разрешены только файлы внутри каталогов из
`KONTUR_ARTIFACT_ALLOWED_ROOTS` и не больше `KONTUR_ARTIFACT_MAX_BYTES`.

## Безопасность

- API по умолчанию слушает только `127.0.0.1`.
- Бот отвечает только numeric user ID из allowlist.
- Секреты не должны попадать в события, Git или логи.
- Telegram не является хранилищем истины.
- MVP не выполняет внешние действия и не содержит approvals.

## Структура

```text
src/kontur/
  api.py            HTTP API
  config.py         environment configuration
  db.py             SQLite schema and repository
  models.py         domain contracts
  service.py        application rules
  telegram_bot.py   commands and callback handlers
  worker.py         reliable Telegram delivery
```

Шаблоны n8n находятся в [`n8n/`](n8n/) и поставляются выключенными.

## Постоянный запуск на macOS

После заполнения `.env` и проверки ручного запуска:

```bash
kontur-services render --service api
kontur-services status
kontur-services install
```

Устанавливаются три пользовательских LaunchAgent:

- `com.personal.kontur.api`;
- `com.personal.kontur.bot`;
- `com.personal.kontur.worker`.

Логи сохраняются в `runtime/logs`. Удаление:

```bash
kontur-services uninstall
```

Перед `install` нужно остановить вручную запущенные процессы, иначе API или
Telegram polling столкнутся с уже занятыми ресурсами.
