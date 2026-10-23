"""Уведомления в Telegram: очередь отправки, бот для привязки аккаунтов и утренняя сводка."""

import json
import logging
import queue
import sqlite3
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from html import escape

from . import db
from .i18n import MONTHS, normalize_lang, tr

log = logging.getLogger("telegram")

STATUS_TITLES = {"todo": "To Do", "in_progress": "In Progress", "review": "Review", "done": "Done"}
def help_text(lang: str) -> str:
    return "\n".join([
        tr("Я присылаю уведомления с доски задач.", lang),
        "",
        tr("/tasks — мои открытые задачи", lang),
        tr("/stop — отключить уведомления", lang),
    ])


def not_linked(lang: str) -> str:
    return tr("Этот чат не привязан к доске. Откройте доску → «Люди» → свой профиль → «Подключить Telegram».", lang)


def short_date(iso: str, lang: str = "ru") -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS[lang][d.month - 1]}"


class NullNotifier:
    """Telegram не настроен: уведомления молча пропускаются."""

    enabled = False
    bot_username = None

    def send(self, chat_id: int, text: str) -> None:
        pass

    def start(self, db_path: str) -> None:
        pass

    def stop(self) -> None:
        pass


# ---------- логика бота, не зависящая от сети ----------

def handle_update(conn: sqlite3.Connection, update: dict) -> list[tuple[int, str]]:
    """Обрабатывает входящее сообщение боту. Возвращает ответы (chat_id, текст)."""
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    text = (msg.get("text") or "").strip()
    if chat_id is None or not text:
        return []
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()
    user = conn.execute("SELECT * FROM users WHERE telegram_chat_id = ?", (chat_id,)).fetchone()
    # Привязанному человеку — на языке доски, остальным — на языке их Telegram.
    lang = user["ui_lang"] if user else normalize_lang((msg.get("from") or {}).get("language_code"))

    if cmd == "/start" and arg:
        target = conn.execute("SELECT * FROM users WHERE telegram_link_code = ?", (arg,)).fetchone()
        if target is None:
            return [(chat_id, tr("Ссылка устарела. Получите новую в своём профиле на доске.", lang))]
        conn.execute("UPDATE users SET telegram_chat_id = NULL WHERE telegram_chat_id = ?", (chat_id,))
        conn.execute(
            "UPDATE users SET telegram_chat_id = ?, telegram_link_code = NULL WHERE id = ?",
            (chat_id, target["id"]),
        )
        conn.commit()
        lang = target["ui_lang"]
        greeting = tr("Привет, {name}! Теперь сюда будут приходить уведомления о ваших задачах.", lang,
                      name=escape(target["display_name"]))
        return [(chat_id, f"{greeting}\n\n{help_text(lang)}")]

    if user is None:
        return [(chat_id, not_linked(lang))]
    if cmd == "/tasks":
        return [(chat_id, my_tasks_text(conn, user["id"], lang))]
    if cmd == "/stop":
        conn.execute("UPDATE users SET telegram_chat_id = NULL WHERE id = ?", (user["id"],))
        conn.commit()
        return [(chat_id, tr("Уведомления отключены. Подключить снова можно в профиле на доске.", lang))]
    return [(chat_id, help_text(lang))]


def task_line(t: sqlite3.Row, lang: str) -> str:
    line = f"• {escape(t['title'])}"
    if t["due_date"]:
        line += tr(" — до {date}", lang, date=short_date(t["due_date"], lang))
    return line


def my_tasks_text(conn: sqlite3.Connection, user_id: int, lang: str) -> str:
    rows = conn.execute(
        """SELECT * FROM tasks WHERE assignee_id = ? AND status != 'done'
           ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,
                    due_date IS NULL, due_date, position""",
        (user_id,),
    ).fetchall()
    if not rows:
        return tr("Открытых задач нет. Отдыхайте 🙂", lang)
    parts, current = [], None
    for t in rows:
        if t["status"] != current:
            current = t["status"]
            parts.append(f"\n<b>{STATUS_TITLES[current]}</b>")
        parts.append(task_line(t, lang))
    return tr("Ваши задачи:", lang) + "\n".join(parts)


def build_digest(conn: sqlite3.Connection, today: date) -> list[tuple[int, str]]:
    """Утренняя сводка: что нужно сделать сегодня и что просрочено."""
    messages = []
    users = conn.execute("SELECT * FROM users WHERE telegram_chat_id IS NOT NULL").fetchall()
    for u in users:
        lang = u["ui_lang"]
        rows = conn.execute(
            """SELECT * FROM tasks WHERE assignee_id = ? AND status != 'done'
               AND due_date IS NOT NULL AND due_date <= ? ORDER BY due_date, position""",
            (u["id"], (today + timedelta(days=1)).isoformat()),
        ).fetchall()
        overdue = [t for t in rows if t["due_date"] < today.isoformat()]
        due_today = [t for t in rows if t["due_date"] == today.isoformat()]
        tomorrow = [t for t in rows if t["due_date"] > today.isoformat()]
        if not rows:
            continue
        parts = [tr("☀️ Доброе утро! Вот что на сегодня.", lang)]
        for title, group in (("Сегодня", due_today), ("Просрочено", overdue), ("Завтра", tomorrow)):
            if group:
                parts.append(f"\n<b>{tr(title, lang)}</b>")
                parts += [task_line(t, lang) for t in group]
        messages.append((u["telegram_chat_id"], "\n".join(parts)))
    return messages


# ---------- работа с сетью ----------

class TelegramBot:
    enabled = True

    def __init__(self, token: str, digest_hour: int = 9, public_url: str | None = None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.digest_hour = digest_hour
        self.public_url = public_url.rstrip("/") if public_url else None
        self.bot_username: str | None = None
        self.queue: queue.Queue = queue.Queue()
        self.stopping = threading.Event()

    def call(self, method: str, http_timeout: float = 15, **params):
        req = urllib.request.Request(
            self.base + method,
            data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            body = json.load(resp)
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "Telegram API error"))
        return body["result"]

    def send(self, chat_id: int, text: str) -> None:
        if self.public_url:
            text += f"\n\n{self.public_url}"
        self.queue.put((chat_id, text))

    def start(self, db_path: str) -> None:
        self.db_path = db_path
        for target in (self._sender, self._poller, self._scheduler):
            threading.Thread(target=target, daemon=True, name=f"tg{target.__name__}").start()

    def stop(self) -> None:
        self.stopping.set()

    def _sender(self) -> None:
        while not self.stopping.is_set():
            try:
                chat_id, text = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            for attempt in range(3):
                try:
                    self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                              disable_web_page_preview=True)
                    break
                except Exception as exc:  # сеть, лимиты, заблокированный бот
                    log.warning("sendMessage to %s failed: %s", chat_id, exc)
                    time.sleep(2 ** attempt)

    def _poller(self) -> None:
        offset = 0
        while not self.stopping.is_set():
            try:
                if self.bot_username is None:
                    self.bot_username = self.call("getMe")["username"]
                    log.info("Telegram bot @%s connected", self.bot_username)
                updates = self.call("getUpdates", http_timeout=40, offset=offset, timeout=30)
            except Exception as exc:
                if self.bot_username is None:
                    log.warning("Не удаётся подключиться к Telegram, проверьте TELEGRAM_BOT_TOKEN: %s", exc)
                    self.stopping.wait(60)
                else:
                    log.warning("getUpdates failed: %s", exc)
                    self.stopping.wait(10)
                continue
            if not updates:
                continue
            conn = db.connect(self.db_path)
            try:
                for upd in updates:
                    offset = upd["update_id"] + 1
                    for chat_id, text in handle_update(conn, upd):
                        self.queue.put((chat_id, text))
            except Exception:
                log.exception("failed to handle Telegram update")
            finally:
                conn.close()

    def _scheduler(self) -> None:
        while not self.stopping.wait(30):
            now = datetime.now()
            if not (self.digest_hour <= now.hour < self.digest_hour + 3):
                continue
            conn = db.connect(self.db_path)
            try:
                today = now.date().isoformat()
                row = conn.execute("SELECT value FROM settings WHERE key = 'last_digest'").fetchone()
                if row and row["value"] == today:
                    continue
                for chat_id, text in build_digest(conn, now.date()):
                    self.send(chat_id, text)
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES ('last_digest', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (today,),
                )
                conn.commit()
            except Exception:
                log.exception("digest failed")
            finally:
                conn.close()
