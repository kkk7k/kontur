from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from kontur.config import Settings
from kontur.db import Database
from kontur.formatting import format_event

LOGGER = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(self, database: Database, bot: Bot) -> None:
        self.database = database
        self.bot = bot

    async def deliver_once(self) -> int:
        now = datetime.now(UTC)
        claimed: list[sqlite3.Row] = []
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM deliveries
                WHERE status IN ('pending', 'retry')
                AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY id LIMIT 20
                """,
                (now.isoformat(),),
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE deliveries SET status = 'sending', updated_at = ?
                    WHERE id = ? AND status IN ('pending', 'retry')
                    """,
                    (now.isoformat(), row["id"]),
                )
                if updated.rowcount:
                    claimed.append(row)

        for delivery in claimed:
            await self._deliver(delivery)
        return len(claimed)

    async def _deliver(self, delivery: sqlite3.Row) -> None:
        event = self.database.get_event(delivery["event_id"])
        if event is None:
            self._fail_permanently(delivery["id"], "event not found")
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Обработано",
                        callback_data=f"resolve:{event.id}",
                    )
                ]
            ]
        )
        try:
            message = await self.bot.send_message(
                chat_id=int(delivery["recipient"]),
                text=format_event(event),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        except Exception as error:
            LOGGER.warning("telegram delivery failed: %s", type(error).__name__)
            self._schedule_retry(delivery, type(error).__name__)
            return

        now = datetime.now(UTC).isoformat()
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE deliveries
                SET status = 'sent', attempts = attempts + 1,
                    external_message_id = ?, sent_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(message.message_id), now, now, delivery["id"]),
            )
            connection.execute(
                """
                UPDATE events SET status = 'notified', updated_at = ?
                WHERE id = ? AND status IN ('new', 'delivery_failed')
                """,
                (now, event.id),
            )

    def _schedule_retry(self, delivery: sqlite3.Row, safe_error: str) -> None:
        attempts = int(delivery["attempts"]) + 1
        terminal = attempts >= 5
        delay = min(15 * (2 ** (attempts - 1)), 3600)
        next_attempt = datetime.now(UTC) + timedelta(seconds=delay)
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE deliveries
                SET status = ?, attempts = ?, next_attempt_at = ?,
                    last_error_safe = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "failed" if terminal else "retry",
                    attempts,
                    None if terminal else next_attempt.isoformat(),
                    safe_error,
                    datetime.now(UTC).isoformat(),
                    delivery["id"],
                ),
            )
            if terminal:
                connection.execute(
                    """
                    UPDATE events SET status = 'delivery_failed', updated_at = ?
                    WHERE id = ?
                    """,
                    (datetime.now(UTC).isoformat(), delivery["event_id"]),
                )

    def _fail_permanently(self, delivery_id: int, safe_error: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE deliveries
                SET status = 'failed', last_error_safe = ?, updated_at = ?
                WHERE id = ?
                """,
                (safe_error, datetime.now(UTC).isoformat(), delivery_id),
            )


async def run() -> None:
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise RuntimeError("KONTUR_TELEGRAM_BOT_TOKEN is required")
    database = Database(settings.database_path)
    database.initialize()
    bot = Bot(settings.telegram_bot_token)
    worker = DeliveryWorker(database, bot)
    try:
        while True:
            await worker.deliver_once()
            await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        await bot.session.close()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOGGER.info("Delivery worker stopped")


if __name__ == "__main__":
    main()
