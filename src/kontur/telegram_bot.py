from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from kontur.config import Settings
from kontur.db import Database
from kontur.formatting import format_event
from kontur.models import EventStatus
from kontur.service import KonturService

LOGGER = logging.getLogger(__name__)


def create_router(
    service: KonturService,
    allowed_users: frozenset[int],
    *,
    discovery_mode: bool = False,
) -> Router:
    router = Router()

    def authorized(user_id: int | None) -> bool:
        return user_id is not None and user_id in allowed_users

    @router.message(F.text, ~F.text.startswith("/"))
    async def discover_user(message: Message) -> None:
        if not discovery_mode or not message.from_user:
            return
        user_id = message.from_user.id
        if authorized(user_id):
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
        if not authorized(message.from_user.id if message.from_user else None):
            LOGGER.warning("telegram access denied")
            return
        await message.answer(
            "Контур подключён.\n\n"
            "/today — последние события\n"
            "/inbox — события, требующие действия\n"
            "/errors — ошибки\n"
            "/digest — итог за текущий день\n"
            "/status — состояние системы\n"
            "/help — все команды"
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if not authorized(message.from_user.id if message.from_user else None):
            return
        await message.answer(
            "/today — события за текущий список\n"
            "/inbox — необработанные события\n"
            "/runs — завершения запусков\n"
            "/errors — ошибки\n"
            "/digest — создать итог за текущий день\n"
            "/status — здоровье Контур Core"
        )

    async def send_events(message: Message, *, event_type: str | None = None) -> None:
        if not authorized(message.from_user.id if message.from_user else None):
            return
        events = service.events(event_type=event_type, limit=10).items
        if not events:
            await message.answer("Событий нет.")
            return
        for event in events:
            await message.answer(format_event(event), parse_mode=ParseMode.HTML)

    @router.message(Command("today"))
    async def today(message: Message) -> None:
        await send_events(message)

    @router.message(Command("runs"))
    async def runs(message: Message) -> None:
        await send_events(message, event_type="run_completed")

    @router.message(Command("errors"))
    async def errors(message: Message) -> None:
        if not authorized(message.from_user.id if message.from_user else None):
            return
        events = [
            event
            for event in service.events(limit=50).items
            if event.severity.value in {"error", "critical"}
        ][:10]
        if not events:
            await message.answer("Ошибок нет.")
            return
        for event in events:
            await message.answer(format_event(event), parse_mode=ParseMode.HTML)

    @router.message(Command("inbox"))
    async def inbox(message: Message) -> None:
        if not authorized(message.from_user.id if message.from_user else None):
            return
        events = [
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
        ][:10]
        if not events:
            await message.answer("Inbox пуст.")
            return
        for event in events:
            await message.answer(format_event(event), parse_mode=ParseMode.HTML)

    @router.message(Command("status"))
    async def status_command(message: Message) -> None:
        if not authorized(message.from_user.id if message.from_user else None):
            return
        state = service.status()
        await message.answer(
            "Контур: OK\n"
            f"Database: {state.database.upper()}\n"
            f"Pending inbox: {state.pending_inbox}\n"
            f"Failed deliveries: {state.failed_deliveries}"
        )

    @router.message(Command("digest"))
    async def digest_command(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else None
        if not authorized(user_id):
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
        if not authorized(user_id):
            await callback.answer("Нет доступа", show_alert=True)
            return
        event_id = callback.data.split(":", 1)[1] if callback.data else ""
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
    if not settings.telegram_allowed_user_ids and not settings.telegram_discovery_mode:
        raise RuntimeError(
            "KONTUR_TELEGRAM_ALLOWED_USER_IDS is required unless discovery mode is enabled"
        )
    database = Database(settings.database_path)
    database.initialize()
    service = KonturService(
        database,
        settings.telegram_allowed_user_ids,
        timezone=settings.timezone,
    )
    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(
            service,
            settings.telegram_allowed_user_ids,
            discovery_mode=settings.telegram_discovery_mode,
        )
    )
    await dispatcher.start_polling(bot)


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level)
    asyncio.run(run())


if __name__ == "__main__":
    main()
