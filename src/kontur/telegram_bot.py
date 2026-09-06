from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BotCommand, CallbackQuery, Message

from kontur.config import Settings
from kontur.db import Database
from kontur.formatting import format_event_list
from kontur.models import EventStatus
from kontur.registry import Registry
from kontur.service import KonturService

LOGGER = logging.getLogger(__name__)


def create_router(
    service: KonturService,
    owner_users: frozenset[int],
    vacancy_users: frozenset[int] = frozenset(),
    *,
    discovery_mode: bool = False,
) -> Router:
    router = Router()

    def is_owner(user_id: int | None) -> bool:
        return user_id is not None and user_id in owner_users

    def is_vacancy_user(user_id: int | None) -> bool:
        return user_id is not None and user_id in vacancy_users

    @router.message(Command("whoami"))
    async def whoami(message: Message) -> None:
        if message.from_user:
            await message.answer(
                f"Ваш Telegram user ID: <code>{message.from_user.id}</code>",
                parse_mode=ParseMode.HTML,
            )

    @router.message(F.text, ~F.text.startswith("/"))
    async def discover_user(message: Message) -> None:
        if not discovery_mode or not message.from_user:
            return
        user_id = message.from_user.id
        if is_owner(user_id) or is_vacancy_user(user_id):
            await message.answer("Вы уже добавлены в allowlist.")
            return
        LOGGER.warning("telegram discovery user_id=%s", user_id)
        await message.answer(
            f"Ваш Telegram user ID: <code>{user_id}</code>\n\n"
            "Доступ к Контур пока не выдан. Добавьте этот ID в allowlist "
            "и отключите discovery mode.",
            parse_mode=ParseMode.HTML,
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else None
        if is_vacancy_user(user_id) and not is_owner(user_id):
            await message.answer(
                "Контур вакансий подключён.\n\n"
                "Сюда будут приходить только новые подходящие вакансии.\n"
                "/whoami — показать Telegram ID\n"
                "/help — справка"
            )
            return
        if not is_owner(user_id):
            LOGGER.warning("telegram access denied")
            return
        await message.answer(
            "Контур подключён.\n\n"
            "/today — последние события\n"
            "/inbox — события, требующие действия\n"
            "/runs — завершения запусков\n"
            "/errors — ошибки\n"
            "/digest — итог за текущий день\n"
            "/agents — реестр агентов\n"
            "/automations — автоматизации и расписания\n"
            "/status — состояние системы\n"
            "/stats — температура/RAM/диск хостов\n"
            "/whoami — показать Telegram ID\n"
            "/help — все команды"
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else None
        if is_vacancy_user(user_id) and not is_owner(user_id):
            await message.answer(
                "Вам будут приходить только новые подходящие вакансии.\n"
                "/whoami — показать Telegram ID"
            )
            return
        if not is_owner(user_id):
            return
        await message.answer(
            "/today — события за текущий список\n"
            "/inbox — необработанные события\n"
            "/runs — завершения запусков\n"
            "/errors — ошибки\n"
            "/digest — создать итог за текущий день\n"
            "/agents — агенты и последние запуски\n"
            "/automations — состояние и расписания\n"
            "/whoami — показать Telegram ID\n"
            "/status — здоровье Контур Core\n"
            "/stats — температура/RAM/диск хостов"
        )

    async def send_events(message: Message, *, event_type: str | None = None) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        result = service.events(event_type=event_type, limit=10)
        if not result.items:
            await message.answer("Событий нет.")
            return
        header = f"Последние события (показано {len(result.items)} из {result.total}):"
        text = format_event_list(result.items, header=header, timezone=service.timezone)
        await message.answer(text, parse_mode=ParseMode.HTML)

    @router.message(Command("today"))
    async def today(message: Message) -> None:
        await send_events(message)

    @router.message(Command("runs"))
    async def runs(message: Message) -> None:
        await send_events(message, event_type="run_completed")

    @router.message(Command("errors"))
    async def errors(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        matching = [
            event
            for event in service.events(limit=50).items
            if event.severity.value in {"error", "critical"}
        ]
        events = matching[:10]
        if not events:
            await message.answer("Ошибок нет.")
            return
        header = (
            f"Последние ошибки (показано {len(events)} из {len(matching)} "
            "за последние 50 событий):"
        )
        text = format_event_list(events, header=header, timezone=service.timezone)
        await message.answer(text, parse_mode=ParseMode.HTML)

    @router.message(Command("inbox"))
    async def inbox(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        matching = [
            event
            for event in service.events(limit=50).items
            if event.requires_action
            and event.status
            in {
                EventStatus.NEW,
                EventStatus.NOTIFIED,
                EventStatus.SEEN,
                EventStatus.DELIVERY_FAILED,
            }
        ]
        events = matching[:10]
        if not events:
            await message.answer("Inbox пуст.")
            return
        header = f"Inbox (показано {len(events)} из {len(matching)} за последние 50 событий):"
        text = format_event_list(events, header=header, timezone=service.timezone)
        await message.answer(text, parse_mode=ParseMode.HTML)

    @router.message(Command("status"))
    async def status_command(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        state = service.status()
        await message.answer(
            "Контур: OK\n"
            f"Database: {state.database.upper()}\n"
            f"Pending inbox: {state.pending_inbox}\n"
            f"Failed deliveries: {state.failed_deliveries}"
        )

    @router.message(Command("stats"))
    async def stats_command(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        events = service.events(event_type="host_metric_reported", limit=50).items
        latest_by_host: dict[str, object] = {}
        for event in events:
            latest_by_host.setdefault(event.producer, event)
        if not latest_by_host:
            await message.answer("Метрик хостов пока нет.")
            return
        lines = ["<b>Хосты</b>"]
        for producer, event in latest_by_host.items():
            host = producer.removeprefix("host-")
            payload = event.payload
            age_minutes = int(
                (
                    datetime.now(event.occurred_at.tzinfo) - event.occurred_at
                ).total_seconds()
                // 60
            )
            temp = (
                f"{payload['temp_avg']}°C (min {payload['temp_min']} / max {payload['temp_max']})"
                if "temp_avg" in payload
                else "н/д"
            )
            uptime_seconds = payload.get("uptime_seconds")
            if uptime_seconds is not None:
                hours, minutes = divmod(int(uptime_seconds) // 60, 60)
                uptime = f"{hours}:{minutes:02d}"
            else:
                uptime = "?"
            lines.append(
                f"\n🖥 <b>{escape(host)}</b> (отчёт {age_minutes} мин назад)\n"
                f"CPU: {temp}\n"
                f"RAM: {payload.get('ram_used_gb', '?')}G/{payload.get('ram_total_gb', '?')}G\n"
                f"Swap: {payload.get('swap_used_mb', '?')}MB\n"
                f"Disk: {payload.get('disk_used_pct', '?')}%\n"
                f"Uptime: {uptime}"
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    def display_time(value) -> str:
        if value is None:
            return "—"
        return value.astimezone().strftime("%d.%m %H:%M")

    @router.message(Command("agents"))
    async def agents_command(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        lines = ["<b>Агенты</b>"]
        for agent in service.agents():
            icon = {
                "active": "🟢",
                "planned": "⚪️",
                "paused": "⏸",
                "failed": "🔴",
            }.get(agent.status.value, "⚪️")
            lines.append(
                f"\n{icon} <b>{agent.name}</b>\n"
                f"Статус: {agent.status.value}\n"
                f"Последний запуск: {display_time(agent.last_run_at)}"
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    @router.message(Command("automations"))
    async def automations_command(message: Message) -> None:
        if not is_owner(message.from_user.id if message.from_user else None):
            return
        lines = ["<b>Автоматизации</b>"]
        for automation in service.automations():
            icon = {
                "healthy": "🟢",
                "active": "🟢",
                "paused": "⏸",
                "stale": "🔴",
                "failed": "🔴",
                "unknown": "⚪️",
            }.get(automation.status.value, "⚪️")
            lines.append(
                f"\n{icon} <b>{automation.name}</b>\n"
                f"Статус: {automation.status.value}\n"
                f"Последний запуск: {display_time(automation.last_run_at)}\n"
                f"Следующий запуск: {display_time(automation.next_run_at)}"
            )
        await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)

    @router.message(Command("digest"))
    async def digest_command(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else None
        if not is_owner(user_id):
            return
        event, created = service.create_daily_digest(producer=f"telegram-{user_id}")
        if event is None:
            await message.answer("За сегодня пока нет событий для digest.")
            return
        if created:
            await message.answer("Digest сформирован и поставлен в очередь доставки.")
        else:
            await message.answer("Сегодняшний digest уже сформирован.")

    @router.callback_query(F.data.startswith("resolve:"))
    async def resolve_callback(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        event_id = callback.data.split(":", 1)[1] if callback.data else ""
        current_event = service.event(event_id)
        can_resolve = is_owner(user_id) or (
            is_vacancy_user(user_id)
            and current_event is not None
            and current_event.type == "vacancy_found"
        )
        if not can_resolve:
            await callback.answer("Нет доступа", show_alert=True)
            return
        try:
            event = service.transition(event_id, EventStatus.RESOLVED, actor_id=str(user_id))
        except ValueError:
            event = service.event(event_id)
        if event is None:
            await callback.answer("Событие не найдено", show_alert=True)
            return
        await callback.answer("Обработано")
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)

    return router


async def run() -> None:
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise RuntimeError("KONTUR_TELEGRAM_BOT_TOKEN is required")
    if (
        not settings.telegram_allowed_user_ids
        and not settings.telegram_vacancy_user_ids
        and not settings.telegram_discovery_mode
    ):
        raise RuntimeError(
            "At least one Telegram allowlist is required unless discovery mode is enabled"
        )
    database = Database(settings.database_path)
    database.initialize()
    registry = Registry(database, settings.timezone)
    service = KonturService(
        database,
        settings.telegram_allowed_user_ids,
        timezone=settings.timezone,
        vacancy_recipients=settings.telegram_vacancy_user_ids,
    )
    session = (
        AiohttpSession(proxy=settings.telegram_proxy_url)
        if settings.telegram_proxy_url
        else None
    )
    bot = Bot(settings.telegram_bot_token, session=session)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            service,
            settings.telegram_allowed_user_ids,
            settings.telegram_vacancy_user_ids,
            discovery_mode=settings.telegram_discovery_mode,
        )
    )
    await bot.set_my_commands(
        [
            BotCommand(command="today", description="Последние события"),
            BotCommand(command="inbox", description="Требуют действия"),
            BotCommand(command="runs", description="Завершения запусков"),
            BotCommand(command="errors", description="Ошибки"),
            BotCommand(command="digest", description="Итог за сегодня"),
            BotCommand(command="agents", description="Реестр агентов"),
            BotCommand(command="automations", description="Автоматизации"),
            BotCommand(command="status", description="Состояние Контура"),
            BotCommand(command="stats", description="Температура/RAM/диск хостов"),
            BotCommand(command="whoami", description="Мой Telegram ID"),
            BotCommand(command="help", description="Все команды"),
        ]
    )

    async def heartbeat() -> None:
        while True:
            registry.heartbeat("kontur-bot")
            await asyncio.sleep(30)

    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        await dispatcher.start_polling(bot)
    finally:
        heartbeat_task.cancel()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
