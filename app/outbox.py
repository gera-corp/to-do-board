"""Отправка уведомлений из очереди в базе: Telegram и почта, с повторами при сбоях."""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable, Optional, Protocol

from . import db
from .i18n import tr
import json

from .notify import Message, build_digest, iso, render

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

    def digest_address(self, user: sqlite3.Row) -> Optional[str]:
        return self.address(user)

    def deliver(self, address: str, message: Message, link: Optional[str]) -> None:
        text = "\n".join(message.lines)
        if link:
            text += f"\n\n{link}"
        self.notifier.send_now(int(address), text)


class EmailChannel:
    name = "email"

    def __init__(self, mailer, site_title: Callable[[], str], public_url: Optional[str]):
        self.mailer = mailer
        self.site_title = site_title
        self.public_url = public_url.rstrip("/") if public_url else None

    def address(self, user: sqlite3.Row) -> Optional[str]:
        return user["email"] if user["email"] and user["email_notify"] else None

    def digest_address(self, user: sqlite3.Row) -> Optional[str]:
        # Сводка на почту — отдельная настройка, по умолчанию выключена.
        return user["email"] if user["email"] and user["email_digest"] else None

    def deliver(self, address: str, message: Message, link: Optional[str]) -> None:
        lang = message.lang
        title = self.site_title() or tr("Доска задач", lang)
        subject = " ".join(f"[{title}] {message.subject}".split())
        text = "\n".join(message.text_lines)
        body = "<br>\n".join(message.lines)
        links = []
        if link:
            text += f"\n\n{tr('Открыть задачу', lang)}: {link}"
            body += (f'<p style="margin:24px 0"><a href="{escape(link)}" style="background:#2F5BD3;color:#fff;'
                     f'padding:10px 18px;border-radius:6px;text-decoration:none">{tr("Открыть задачу", lang)}</a></p>')
            links.append(f'<a href="{escape(link)}">{tr("Перестать наблюдать", lang)}</a>')
        if self.public_url:
            profile = f"{self.public_url}/#profile"
            text += f"\n{tr('Настройки уведомлений', lang)}: {profile}"
            links.append(f'<a href="{escape(profile)}">{tr("Настройки уведомлений", lang)}</a>')
        if links:
            body += f'<p style="color:#666;font-size:12px">{" · ".join(links)}</p>'
        html = f'<div style="font-family:sans-serif;font-size:15px;line-height:1.5">{body}</div>'
        thread = f"task-{message.task_id}" if message.task_id else None
        self.mailer.send(address, subject, text, html, thread)


class Dispatcher:
    def __init__(self, db_path: str, channels: list, public_url: Optional[str],
                 clock: Callable[[], datetime], digest_hour: int = 9):
        self.db_path = db_path
        self.digest_hour = digest_hour
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

    def run_digest(self, now: Optional[datetime] = None) -> None:
        """Раз в день, в час сводки по времени сервера, кладёт утреннюю сводку в очередь."""
        local = (now or self.clock()).astimezone()
        if not (self.digest_hour <= local.hour < self.digest_hour + 3):
            return
        conn = db.connect(self.db_path)
        try:
            today = local.date().isoformat()
            row = conn.execute("SELECT value FROM settings WHERE key = 'last_digest'").fetchone()
            if row and row["value"] == today:
                return
            stamp = iso(now or self.clock())
            for user_id, message in build_digest(conn, local.date()):
                user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
                for channel in self.channels.values():
                    if channel.digest_address(user) is None:
                        continue
                    conn.execute(
                        """INSERT INTO outbox (user_id, channel, task_id, task_title, kind, payload, created_at, due_at)
                           VALUES (?, ?, NULL, ?, 'digest', ?, ?, ?)""",
                        (user_id, channel.name, message.subject,
                         json.dumps({"lines": message.lines}, ensure_ascii=False), stamp, stamp),
                    )
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('last_digest', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (today,),
            )
            conn.commit()
        finally:
            conn.close()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="outbox").start()

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        while not self.stopping.wait(POLL_SECONDS):
            try:
                self.run_digest()
                self.deliver_due()
            except Exception:
                log.exception("outbox delivery failed")
