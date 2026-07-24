from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from kontur.config import Settings
from kontur.db import Database
from kontur.formatting import format_event
from kontur.models import EventCreate

LOGGER = logging.getLogger(__name__)


class DeliveryWorker:
    def __init__(
        self,
        database: Database,
        bot: Bot,
        *,
        recipients: frozenset[int] = frozenset(),
        night_agent_outbox: Path | None = None,
        artifact_allowed_roots: tuple[Path, ...] = (),
        artifact_max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.database = database
        self.bot = bot
        self.recipients = recipients
        self.night_agent_outbox = night_agent_outbox
        self.artifact_allowed_roots = tuple(root.resolve() for root in artifact_allowed_roots)
        self.artifact_max_bytes = artifact_max_bytes

    async def deliver_once(self) -> int:
        self.import_night_agent_outbox()
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
            await self._send_artifacts(event, int(delivery["recipient"]))
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

    def import_night_agent_outbox(self) -> int:
        if self.night_agent_outbox is None:
            return 0
        outbox = self.night_agent_outbox
        if not outbox.exists():
            return 0
        processed_dir = outbox / "processed"
        failed_dir = outbox / "failed"
        imported = 0
        for source in sorted(outbox.glob("*.json")):
            processing = source.with_suffix(".processing")
            try:
                source.replace(processing)
            except FileNotFoundError:
                continue
            try:
                event = EventCreate.model_validate_json(processing.read_text(encoding="utf-8"))
                saved, _ = self.database.create_event(event, self.recipients)
                self.database.ensure_deliveries(saved.id, self.recipients)
            except Exception as error:
                LOGGER.warning("night agent outbox import failed: %s", type(error).__name__)
                failed_dir.mkdir(parents=True, exist_ok=True)
                processing.replace(failed_dir / source.name)
                continue
            processed_dir.mkdir(parents=True, exist_ok=True)
            processing.replace(processed_dir / source.name)
            imported += 1
        return imported

    async def _send_artifacts(self, event: EventCreate, recipient: int) -> None:
        for artifact in event.artifacts:
            path = self._safe_artifact_path(artifact.uri)
            if path is None:
                continue
            try:
                await self.bot.send_document(
                    chat_id=recipient,
                    document=FSInputFile(path, filename=path.name),
                    caption=artifact.title[:1024],
                )
            except Exception as error:
                LOGGER.warning(
                    "telegram artifact delivery failed event_id=%s error=%s",
                    event.id,
                    type(error).__name__,
                )

    def _safe_artifact_path(self, uri: str) -> Path | None:
        path = Path(uri).expanduser()
        if not path.is_absolute():
            return None
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_file():
            return None
        if not any(resolved.is_relative_to(root) for root in self.artifact_allowed_roots):
            LOGGER.warning("artifact outside allowlist: %s", resolved)
            return None
        if resolved.stat().st_size > self.artifact_max_bytes:
            LOGGER.warning("artifact exceeds size limit: %s", resolved)
            return None
        return resolved

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
    worker = DeliveryWorker(
        database,
        bot,
        recipients=settings.telegram_allowed_user_ids,
        night_agent_outbox=settings.night_agent_outbox,
        artifact_allowed_roots=settings.artifact_allowed_roots,
        artifact_max_bytes=settings.artifact_max_bytes,
    )
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
