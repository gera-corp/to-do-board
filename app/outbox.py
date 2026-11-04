"""Отправка уведомлений из очереди в базе: Telegram и почта, с повторами при сбоях."""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol

from . import db
from .notify import Message, iso, render

log = logging.getLogger("outbox")

POLL_SECONDS = 5
GIVE_UP = timedelta(hours=24)


def retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** attempts, 3600))


def parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


class Channel(Protocol):
    name: str

    def address(self, user: sqlite3.Row) -> Optional[str]: ...

    def deliver(self, address: str, message: Message, link: Optional[str]) -> None: ...


class TelegramChannel:
    name = "telegram"

    def __init__(self, notifier):
        self.notifier = notifier

    def address(self, user: sqlite3.Row) -> Optional[str]:
        if not self.notifier.enabled or user["telegram_chat_id"] is None:
            return None
        return str(user["telegram_chat_id"])

    def deliver(self, address: str, message: Message, link: Optional[str]) -> None:
        text = "\n".join(message.lines)
        if link:
            text += f"\n\n{link}"
        self.notifier.send_now(int(address), text)


class Dispatcher:
    def __init__(self, db_path: str, channels: list, public_url: Optional[str],
                 clock: Callable[[], datetime]):
        self.db_path = db_path
        self.channels = {c.name: c for c in channels}
        self.public_url = public_url.rstrip("/") if public_url else None
        self.clock = clock
        self.stopping = threading.Event()

    def link(self, rows: list[sqlite3.Row]) -> Optional[str]:
        task_id = rows[-1]["task_id"]
        if not self.public_url or task_id is None or any(r["kind"] == "deleted" for r in rows):
            return None
        return f"{self.public_url}/#task={task_id}"

    def deliver_due(self, now: Optional[datetime] = None) -> int:
        """Отправляет созревшие группы. Возвращает число отправленных сообщений."""
        now = now or self.clock()
        stamp = iso(now)
        sent = 0
        conn = db.connect(self.db_path)
        try:
            groups = conn.execute(
                """SELECT user_id, channel, task_id FROM outbox
                   WHERE next_try_at IS NULL OR next_try_at <= ?
                   GROUP BY user_id, channel, task_id HAVING MIN(due_at) <= ? ORDER BY MIN(id)""",
                (stamp, stamp),
            ).fetchall()
            for g in groups:
                sent += self._deliver_group(conn, g["user_id"], g["channel"], g["task_id"], now)
                conn.commit()
        finally:
            conn.close()
        return sent

    def _deliver_group(self, conn: sqlite3.Connection, user_id: int, channel_name: str,
                       task_id: Optional[int], now: datetime) -> int:
        rows = conn.execute(
            """SELECT * FROM outbox WHERE user_id = ? AND channel = ? AND task_id IS ? AND created_at <= ?
               ORDER BY id""",
            (user_id, channel_name, task_id, iso(now)),
        ).fetchall()
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))

        def drop() -> None:
            conn.execute(f"DELETE FROM outbox WHERE id IN ({marks})", ids)

        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        channel = self.channels.get(channel_name)
        # Человек мог за это время отключить канал или удалить адрес.
        address = channel.address(user) if channel and user else None
        message = render(rows, user["ui_lang"], user_id) if address else None
        if message is None:
            drop()
            return 0
        try:
            channel.deliver(address, message, self.link(rows))
        except Exception as exc:  # сеть, почтовый сервер, заблокированный бот
            oldest = min(parse(r["created_at"]) for r in rows)
            if now - oldest >= GIVE_UP:
                log.warning("Уведомление для %s (%s) не доставлено за 24 часа и снято с очереди: %s",
                            user_id, channel_name, exc)
                drop()
                return 0
            attempts = max(r["attempts"] for r in rows)
            log.warning("Не удалось отправить уведомление для %s (%s), попытка %d: %s",
                        user_id, channel_name, attempts + 1, exc)
            conn.execute(f"UPDATE outbox SET attempts = ?, next_try_at = ? WHERE id IN ({marks})",
                         (attempts + 1, iso(now + retry_delay(attempts)), *ids))
            return 0
        drop()
        return 1

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="outbox").start()

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        while not self.stopping.wait(POLL_SECONDS):
            try:
                self.deliver_due()
            except Exception:
                log.exception("outbox delivery failed")
