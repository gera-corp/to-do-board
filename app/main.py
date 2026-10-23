import hashlib
import hmac
import json
import re
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db, google
from .config import load_env_file
from .recurrence import next_due
from .security import DECOY_PASSWORD_HASH, LoginLimiter, hash_password, new_token, verify_password
from .telegram import NullNotifier, TelegramBot, short_date

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE = "session"
SESSION_DAYS = 90
OAUTH_COOKIE = "oauth_state"
OAUTH_COOKIE_PATH = "/api/auth/google"
OAUTH_STATE_MINUTES = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Status = Literal["todo", "in_progress", "review", "done"]
Priority = Literal["low", "normal", "high"]
Recurrence = Literal["none", "daily", "weekly", "monthly"]
Theme = Literal["auto", "light", "dark", "mint", "kraft", "midnight", "contrast"]
Color = Field(default="#2F5BD3", pattern=r"^#[0-9A-Fa-f]{6}$")
Points = Field(default=1, ge=0, le=100)

# Поля задачи, изменения которых попадают в историю как «edited».
EDITABLE_FIELDS = ("title", "description", "priority", "category", "due_date", "recurrence", "points",
                   "parent_id")
# Как связь выглядит со стороны второй задачи.
INVERSE_LINK = {"blocks": "blocked_by", "blocked_by": "blocks", "relates": "relates"}


# ---------- схемы ----------

def normalize_google_email(value: Optional[str]) -> Optional[str]:
    """Почта Google хранится в нижнем регистре; пустая строка — «отвязать»."""
    if value is None:
        return None
    value = value.strip().lower()
    if value and not EMAIL_RE.match(value):
        raise ValueError("некорректный адрес почты")
    return value


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=32, pattern=r"^[\w.-]+$")
    display_name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    color: str = Color
    is_admin: bool = False
    google_email: Optional[str] = Field(default=None, max_length=254)

    _google_email = field_validator("google_email")(normalize_google_email)


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    password: Optional[str] = Field(default=None, min_length=4, max_length=128)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_admin: Optional[bool] = None
    theme: Optional[Theme] = None
    google_email: Optional[str] = Field(default=None, max_length=254)

    _google_email = field_validator("google_email")(normalize_google_email)


class LoginIn(BaseModel):
    username: str
    password: str


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    status: Status = "todo"
    priority: Priority = "normal"
    category: str = Field(default="", max_length=40)
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None
    recurrence: Recurrence = "none"
    points: int = Points
    checklist: list[str] = Field(default_factory=list, max_length=100)
    parent_id: Optional[int] = None

    @field_validator("checklist")
    @classmethod
    def clean_checklist(cls, items: list[str]) -> list[str]:
        return [i.strip()[:200] for i in items if i.strip()]


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    status: Optional[Status] = None
    priority: Optional[Priority] = None
    category: Optional[str] = Field(default=None, max_length=40)
    assignee_id: Optional[int] = None
    due_date: Optional[date] = None
    recurrence: Optional[Recurrence] = None
    points: Optional[int] = Field(default=None, ge=0, le=100)
    parent_id: Optional[int] = None


class LinkIn(BaseModel):
    # С точки зрения задачи, к которой добавляется связь.
    kind: Literal["blocks", "blocked_by", "relates"]
    task_id: int


class MoveIn(BaseModel):
    status: Status
    index: int = Field(ge=0)


class TextIn(BaseModel):
    text: str = Field(max_length=2000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("пустой текст")
        return v.strip()


class ChecklistUpdate(BaseModel):
    text: Optional[str] = Field(default=None, min_length=1, max_length=200)
    done: Optional[bool] = None


# ---------- вспомогательное ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def user_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "color": row["color"],
        "is_admin": bool(row["is_admin"]),
        "telegram_linked": row["telegram_chat_id"] is not None,
        "theme": row["theme"],
        "google_email": row["google_email"],
    }


def item_out(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "task_id": row["task_id"], "text": row["text"], "done": bool(row["done"])}


def event_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "task_id": row["task_id"], "user_id": row["user_id"], "kind": row["kind"],
        "payload": json.loads(row["payload"]), "created_at": row["created_at"],
    }


def tasks_out(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    tasks = [{k: r[k] for k in r.keys()} for r in rows]
    if not tasks:
        return tasks
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["checklist"] = []
    marks = ",".join("?" * len(by_id))
    items = conn.execute(
        f"SELECT * FROM checklist_items WHERE task_id IN ({marks}) ORDER BY position, id", tuple(by_id)
    ).fetchall()
    for i in items:
        by_id[i["task_id"]]["checklist"].append(item_out(i))
    for t in tasks:
        t["links"] = []
    links = conn.execute(
        f"SELECT * FROM task_links WHERE from_id IN ({marks}) OR to_id IN ({marks}) ORDER BY id",
        (*by_id, *by_id),
    ).fetchall()
    for link in links:
        if link["from_id"] in by_id:
            by_id[link["from_id"]]["links"].append(
                {"id": link["id"], "type": link["kind"], "task_id": link["to_id"]})
        if link["to_id"] in by_id:
            by_id[link["to_id"]]["links"].append(
                {"id": link["id"], "type": INVERSE_LINK[link["kind"]], "task_id": link["from_id"]})
    return tasks


def validate_parent(conn: sqlite3.Connection, task_id: Optional[int], parent_id: Optional[int]) -> None:
    """Подзадачи — ровно один уровень: у подзадачи нет своих подзадач."""
    if parent_id is None:
        return
    if parent_id == task_id:
        raise HTTPException(400, "Задача не может быть подзадачей самой себя")
    parent = conn.execute("SELECT parent_id FROM tasks WHERE id = ?", (parent_id,)).fetchone()
    if parent is None:
        raise HTTPException(400, "Родительской задачи нет")
    if parent["parent_id"] is not None:
        raise HTTPException(400, "Подзадачи бывают только одного уровня: выбрана сама подзадача")
    if task_id is not None and conn.execute(
        "SELECT 1 FROM tasks WHERE parent_id = ? LIMIT 1", (task_id,)
    ).fetchone():
        raise HTTPException(400, "У задачи есть свои подзадачи, её нельзя сделать подзадачей")


def blocking_creates_cycle(conn: sqlite3.Connection, blocker: int, blocked: int) -> bool:
    """Добавление «blocker блокирует blocked» замкнёт цикл, если blocked уже (через цепочку) блокирует blocker."""
    seen, frontier = {blocked}, [blocked]
    while frontier:
        current = frontier.pop()
        if current == blocker:
            return True
        for row in conn.execute("SELECT to_id FROM task_links WHERE kind = 'blocks' AND from_id = ?", (current,)):
            if row["to_id"] not in seen:
                seen.add(row["to_id"])
                frontier.append(row["to_id"])
    return False


def get_task_row(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Задача не найдена")
    return row


def task_full(conn: sqlite3.Connection, task_id: int) -> dict:
    return tasks_out(conn, [get_task_row(conn, task_id)])[0]


def ensure_user_exists(conn: sqlite3.Connection, user_id: Optional[int]) -> None:
    if user_id is not None and conn.execute(
        "SELECT 1 FROM users WHERE id = ?", (user_id,)
    ).fetchone() is None:
        raise HTTPException(400, "Такого пользователя нет")


def column_ids(conn: sqlite3.Connection, status: str, exclude: int) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM tasks WHERE status = ? AND id != ? ORDER BY position, id",
        (status, exclude),
    ).fetchall()
    return [r["id"] for r in rows]


def write_order(conn: sqlite3.Connection, ids: list[int]) -> None:
    conn.executemany(
        "UPDATE tasks SET position = ? WHERE id = ?",
        [(pos, tid) for pos, tid in enumerate(ids)],
    )


def next_position(conn: sqlite3.Connection, table: str, column: str, value) -> int:
    return conn.execute(
        f"SELECT COALESCE(MAX(position), -1) + 1 FROM {table} WHERE {column} = ?", (value,)
    ).fetchone()[0]


def add_checklist(conn: sqlite3.Connection, task_id: int, texts: list[str]) -> None:
    start = next_position(conn, "checklist_items", "task_id", task_id)
    conn.executemany(
        "INSERT INTO checklist_items (task_id, text, position) VALUES (?, ?, ?)",
        [(task_id, text, start + i) for i, text in enumerate(texts)],
    )


@dataclass
class Ctx:
    """Всё, что нужно действию над доской: соединение, кто действует и куда слать уведомления."""

    conn: sqlite3.Connection
    user: sqlite3.Row
    notifier: object

    @property
    def actor(self) -> str:
        return escape(self.user["display_name"])

    def log(self, task_id: int, kind: str, **payload) -> int:
        cur = self.conn.execute(
            "INSERT INTO task_events (task_id, user_id, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, self.user["id"], kind, json.dumps(payload, ensure_ascii=False), now_iso()),
        )
        return cur.lastrowid

    def notify(self, user_ids, text: str) -> None:
        """Шлёт сообщение указанным людям, кроме того, кто сам совершил действие."""
        ids = {u for u in user_ids if u is not None and u != self.user["id"]}
        if not ids or not self.notifier.enabled:
            return
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT telegram_chat_id FROM users WHERE id IN ({marks}) AND telegram_chat_id IS NOT NULL",
            tuple(ids),
        ).fetchall()
        for r in rows:
            self.notifier.send(r["telegram_chat_id"], text)


def quoted(task) -> str:
    return f"«{escape(task['title'])}»"


def notify_assigned(ctx: Ctx, task) -> None:
    text = f"📌 {ctx.actor} поручает вам: {quoted(task)}"
    if task["due_date"]:
        text += f"\nСрок: {short_date(task['due_date'])}"
    ctx.notify([task["assignee_id"]], text)


def change_status(ctx: Ctx, task_id: int, old: str, new: str) -> None:
    """Последствия смены колонки: история, время выполнения, уведомления и следующий повтор."""
    if new == old:
        return
    conn = ctx.conn
    ctx.log(task_id, "status", **{"from": old, "to": new})
    task = get_task_row(conn, task_id)

    if new != "done":
        conn.execute("UPDATE tasks SET completed_at = NULL WHERE id = ?", (task_id,))
        if new == "review":
            ctx.notify([task["creator_id"]], f"👀 {ctx.actor} просит проверить: {quoted(task)}")
        return

    conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (now_iso(), task_id))
    text = f"✅ Сделано: {quoted(task)}"
    if task["assignee_id"] and task["points"]:
        pts = task["points"]
        text += f", +{pts} {plural(pts, 'очко', 'очка', 'очков')}"
    ctx.notify([task["creator_id"], task["assignee_id"]], f"{text}\nОтметил(а): {ctx.actor}")

    notify_unblocked(ctx, task)
    if task["recurrence"] != "none":
        spawn_next(ctx, task)


def notify_unblocked(ctx: Ctx, done_task: sqlite3.Row) -> None:
    """Задачи, которые ждали только эту, можно начинать — говорим их исполнителям."""
    waiting = ctx.conn.execute(
        """SELECT t.* FROM task_links l JOIN tasks t ON t.id = l.to_id
           WHERE l.kind = 'blocks' AND l.from_id = ? AND t.status != 'done'""",
        (done_task["id"],),
    ).fetchall()
    for task in waiting:
        still_blocked = ctx.conn.execute(
            """SELECT 1 FROM task_links l JOIN tasks b ON b.id = l.from_id
               WHERE l.kind = 'blocks' AND l.to_id = ? AND b.status != 'done' LIMIT 1""",
            (task["id"],),
        ).fetchone()
        if not still_blocked and task["assignee_id"]:
            ctx.notify([task["assignee_id"]], f"🔓 Можно начинать: {quoted(task)}\nГотово: {quoted(done_task)}")


def spawn_next(ctx: Ctx, task: sqlite3.Row) -> None:
    conn = ctx.conn
    today = date.today()
    base = date.fromisoformat(task["due_date"]) if task["due_date"] else today
    due = next_due(base, task["recurrence"])
    while due <= today:
        due = next_due(due, task["recurrence"])

    # Повтор переезжает на новую копию, чтобы повторное «Готово» не плодило дубли.
    conn.execute("UPDATE tasks SET recurrence = 'none' WHERE id = ?", (task["id"],))
    cur = conn.execute(
        """INSERT INTO tasks (title, description, status, priority, category, assignee_id,
                              creator_id, due_date, recurrence, points, position, parent_id)
           VALUES (?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (task["title"], task["description"], task["priority"], task["category"],
         task["assignee_id"], task["creator_id"], due.isoformat(), task["recurrence"],
         task["points"], next_position(conn, "tasks", "status", "todo"), task["parent_id"]),
    )
    items = conn.execute(
        "SELECT text FROM checklist_items WHERE task_id = ? ORDER BY position, id", (task["id"],)
    ).fetchall()
    add_checklist(conn, cur.lastrowid, [i["text"] for i in items])
    ctx.log(cur.lastrowid, "created", recurring_from=task["id"])


def period_start(period: str) -> str:
    """Начало недели или месяца по местному времени сервера, в UTC (как хранится completed_at)."""
    now = datetime.now().astimezone()
    if period == "all":
        return ""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight - timedelta(days=now.weekday()) if period == "week" else midnight.replace(day=1)
    return start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def static_version(root: Path) -> str:
    """Отпечаток всех файлов фронтенда: меняется с любым изменением интерфейса."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def make_notifier():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return NullNotifier()
    return TelegramBot(
        token,
        digest_hour=int(os.environ.get("TELEGRAM_DIGEST_HOUR") or 9),
        public_url=os.environ.get("TODO_PUBLIC_URL"),
    )


# ---------- приложение ----------

def create_app(db_path: Optional[str] = None, notifier=None) -> FastAPI:
    load_env_file()
    db_path = db_path or os.environ.get("TODO_DB") or str(Path("data") / "tasks.db")
    notifier = notifier or make_notifier()
    # За HTTPS-ingress cookie сессии должна уходить только по защищённому соединению.
    cookie_secure = os.environ.get("TODO_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
    db.init_db(db_path, journal_mode=os.environ.get("TODO_SQLITE_JOURNAL_MODE") or "WAL")

    # Вход по паролю можно выключить, когда все перейдут на Google.
    password_login = os.environ.get("TODO_PASSWORD_LOGIN", "true").lower() not in ("0", "false", "no")
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    public_url = (os.environ.get("TODO_PUBLIC_URL") or "").rstrip("/")
    # Без адреса доски Google некуда вернуть пользователя, поэтому нужны все три.
    google_enabled = bool(google_client_id and google_client_secret and public_url)
    google_redirect_uri = f"{public_url}/api/auth/google/callback"
    # Попытки считаются и по логину (перебор пароля одного человека с разных адресов),
    # и по IP (перебор разных логинов с одного адреса).
    account_limiter = LoginLimiter(max_failures=10)
    ip_limiter = LoginLimiter(max_failures=30)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        notifier.start(db_path)
        yield
        notifier.stop()

    app = FastAPI(title="Домашний канбан", lifespan=lifespan)

    def get_conn():
        conn = db.connect(db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # scope="function": коммит происходит до отправки ответа, иначе следующий запрос
    # клиента может не увидеть только что записанные данные (например, сессию после входа).
    Conn = Depends(get_conn, scope="function")

    def current_user(request: Request, conn: sqlite3.Connection = Conn) -> sqlite3.Row:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            row = conn.execute(
                """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token = ? AND s.expires_at > ?""",
                (token, now_iso()),
            ).fetchone()
            if row:
                return row
        raise HTTPException(401, "Нужно войти")

    def admin_user(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
        if not user["is_admin"]:
            raise HTTPException(403, "Это может сделать только администратор")
        return user

    def get_ctx(user: sqlite3.Row = Depends(current_user), conn: sqlite3.Connection = Conn) -> Ctx:
        return Ctx(conn, user, notifier)

    def start_session(conn: sqlite3.Connection, response: Response, user_id: int) -> None:
        token = new_token()
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires.strftime("%Y-%m-%d %H:%M:%S")),
        )
        response.set_cookie(
            SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
            secure=cookie_secure,
        )

    def ensure_google_email_free(conn: sqlite3.Connection, email: str, user_id: Optional[int]) -> None:
        row = conn.execute("SELECT id FROM users WHERE google_email = ?", (email,)).fetchone()
        if row and row["id"] != user_id:
            raise HTTPException(409, "Эта Google-почта уже привязана к другому человеку")

    def insert_user(conn: sqlite3.Connection, data: UserCreate, is_admin: bool) -> sqlite3.Row:
        google_email = data.google_email or None
        if google_email:
            ensure_google_email_free(conn, google_email, None)
        try:
            cur = conn.execute(
                """INSERT INTO users (username, display_name, password_hash, color, is_admin, google_email)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (data.username, data.display_name, hash_password(data.password),
                 data.color, int(is_admin), google_email),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Такой логин уже занят")
        return conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()

    # --- первичная настройка и вход ---

    @app.get("/api/health")
    def health(conn: sqlite3.Connection = Conn):
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}

    @app.get("/api/setup")
    def setup_status(conn: sqlite3.Connection = Conn):
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return {"needs_setup": count == 0, "google": google_enabled, "password_login": password_login}

    @app.post("/api/setup", status_code=201)
    def setup(data: UserCreate, response: Response, conn: sqlite3.Connection = Conn):
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            raise HTTPException(409, "Первый пользователь уже создан")
        user = insert_user(conn, data, is_admin=True)
        start_session(conn, response, user["id"])
        return user_out(user)

    @app.post("/api/login")
    def login(data: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Conn):
        if not password_login:
            raise HTTPException(403, "Вход по паролю отключён. Войдите через Google.")
        username = data.username.strip()
        account_key = f"user:{username.lower()}"
        ip_key = f"ip:{request.client.host if request.client else '-'}"
        if account_limiter.blocked(account_key) or ip_limiter.blocked(ip_key):
            raise HTTPException(429, "Слишком много неудачных попыток. Попробуйте через 15 минут.")
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        # Пароль проверяется и для несуществующего логина, чтобы время ответа не выдавало,
        # какие логины есть.
        correct = verify_password(data.password, user["password_hash"] if user else DECOY_PASSWORD_HASH)
        if user is None or not correct:
            account_limiter.fail(account_key)
            ip_limiter.fail(ip_key)
            raise HTTPException(401, "Неверный логин или пароль")
        account_limiter.reset(account_key)
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))
        start_session(conn, response, user["id"])
        return user_out(user)

    # --- вход через Google ---

    def google_failed(reason: str) -> RedirectResponse:
        response = RedirectResponse(f"/?login_error={reason}", status_code=303)
        response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
        return response

    @app.get("/api/auth/google/start", include_in_schema=False)
    def google_start(conn: sqlite3.Connection = Conn):
        if not google_enabled:
            raise HTTPException(404, "Вход через Google не настроен")
        state, nonce = new_token(), new_token()
        verifier, challenge = google.pkce_pair()
        expires = datetime.now(timezone.utc) + timedelta(minutes=OAUTH_STATE_MINUTES)
        conn.execute("DELETE FROM oauth_states WHERE expires_at <= ?", (now_iso(),))
        conn.execute(
            "INSERT INTO oauth_states (state, nonce, verifier, expires_at) VALUES (?, ?, ?, ?)",
            (state, nonce, verifier, expires.strftime("%Y-%m-%d %H:%M:%S")),
        )
        response = RedirectResponse(
            google.auth_url(google_client_id, google_redirect_uri, state, nonce, challenge), status_code=303
        )
        # state привязан к браузеру: чужую ссылку с чужим state подсунуть не выйдет.
        response.set_cookie(
            OAUTH_COOKIE, state, max_age=OAUTH_STATE_MINUTES * 60, path=OAUTH_COOKIE_PATH,
            httponly=True, samesite="lax", secure=cookie_secure,
        )
        return response

    @app.get("/api/auth/google/callback", include_in_schema=False)
    def google_callback(request: Request, state: str = "", code: str = "", error: str = "",
                        conn: sqlite3.Connection = Conn):
        if not google_enabled:
            raise HTTPException(404, "Вход через Google не настроен")
        row = conn.execute("SELECT * FROM oauth_states WHERE state = ?", (state,)).fetchone() if state else None
        if row:
            conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
        if error:
            return google_failed("cancelled")
        cookie_state = request.cookies.get(OAUTH_COOKIE, "")
        if (row is None or not cookie_state or not hmac.compare_digest(cookie_state, state)
                or row["expires_at"] <= now_iso()):
            return google_failed("state")
        try:
            tokens = google.exchange_code(
                code, row["verifier"], google_redirect_uri, google_client_id, google_client_secret
            )
            claims = google.verify_id_token(tokens.get("id_token", ""), google_client_id, row["nonce"])
        except google.GoogleAuthError:
            return google_failed("token")

        user = conn.execute("SELECT * FROM users WHERE google_sub = ?", (claims["sub"],)).fetchone()
        if user is None:
            user = conn.execute(
                "SELECT * FROM users WHERE google_email = ?", (claims["email"].lower(),)
            ).fetchone()
        if user is None:
            return google_failed("not_allowed")
        if user["google_sub"] != claims["sub"]:
            conn.execute("UPDATE users SET google_sub = ? WHERE id = ?", (claims["sub"], user["id"]))

        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(OAUTH_COOKIE, path=OAUTH_COOKIE_PATH)
        start_session(conn, response, user["id"])
        return response

    @app.post("/api/logout", status_code=204)
    def logout(request: Request, response: Response, conn: sqlite3.Connection = Conn):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        response.delete_cookie(SESSION_COOKIE)

    @app.get("/api/me")
    def me(user: sqlite3.Row = Depends(current_user)):
        return user_out(user)

    @app.get("/api/config")
    def config(_: sqlite3.Row = Depends(current_user)):
        return {"telegram": notifier.enabled, "bot_username": notifier.bot_username}

    # --- Telegram ---

    @app.post("/api/me/telegram")
    def telegram_link(ctx: Ctx = Depends(get_ctx)):
        if not notifier.enabled or not notifier.bot_username:
            raise HTTPException(400, "Telegram-бот не настроен или ещё не подключился")
        code = new_token()[:32]
        ctx.conn.execute("UPDATE users SET telegram_link_code = ? WHERE id = ?", (code, ctx.user["id"]))
        return {"url": f"https://t.me/{notifier.bot_username}?start={code}"}

    @app.delete("/api/me/telegram", status_code=204)
    def telegram_unlink(ctx: Ctx = Depends(get_ctx)):
        ctx.conn.execute(
            "UPDATE users SET telegram_chat_id = NULL, telegram_link_code = NULL WHERE id = ?",
            (ctx.user["id"],),
        )

    # --- пользователи ---

    @app.get("/api/users")
    def list_users(_: sqlite3.Row = Depends(current_user), conn: sqlite3.Connection = Conn):
        rows = conn.execute("SELECT * FROM users ORDER BY display_name COLLATE NOCASE").fetchall()
        return [user_out(r) for r in rows]

    @app.post("/api/users", status_code=201)
    def create_user(data: UserCreate, _: sqlite3.Row = Depends(admin_user),
                    conn: sqlite3.Connection = Conn):
        return user_out(insert_user(conn, data, is_admin=data.is_admin))

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: int, data: UserUpdate, user: sqlite3.Row = Depends(current_user),
                    conn: sqlite3.Connection = Conn):
        if user_id != user["id"] and not user["is_admin"]:
            raise HTTPException(403, "Можно менять только свой профиль")
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if target is None:
            raise HTTPException(404, "Пользователь не найден")
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        if "is_admin" in changes:
            if not user["is_admin"]:
                raise HTTPException(403, "Права администратора выдаёт администратор")
            if user_id == user["id"] and not changes["is_admin"]:
                raise HTTPException(400, "Нельзя снять права администратора с себя")
            changes["is_admin"] = int(changes["is_admin"])
        if "password" in changes:
            changes["password_hash"] = hash_password(changes.pop("password"))
        if "google_email" in changes:
            # Почту назначает только администратор: иначе можно привязать к себе чужой адрес.
            if not user["is_admin"]:
                raise HTTPException(403, "Google-почту назначает администратор")
            email = changes["google_email"] or None
            if email:
                ensure_google_email_free(conn, email, user_id)
            changes["google_email"] = email
            if email != target["google_email"]:
                changes["google_sub"] = None
        if changes:
            cols = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*changes.values(), user_id))
        return user_out(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    @app.delete("/api/users/{user_id}", status_code=204)
    def delete_user(user_id: int, user: sqlite3.Row = Depends(admin_user),
                    conn: sqlite3.Connection = Conn):
        if user_id == user["id"]:
            raise HTTPException(400, "Нельзя удалить самого себя")
        if conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount == 0:
            raise HTTPException(404, "Пользователь не найден")

    # --- задачи ---

    @app.get("/api/tasks")
    def list_tasks(assignee_id: Optional[int] = None, category: Optional[str] = None,
                   q: Optional[str] = None, ctx: Ctx = Depends(get_ctx)):
        where, params = [], []
        if assignee_id is not None:
            where.append("assignee_id = ?")
            params.append(assignee_id)
        if category:
            where.append("category = ?")
            params.append(category)
        if q:
            where.append("(title LIKE ? OR description LIKE ?)")
            params += [f"%{q}%", f"%{q}%"]
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY status, position, id"
        return tasks_out(ctx.conn, ctx.conn.execute(sql, params).fetchall())

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int, ctx: Ctx = Depends(get_ctx)):
        return task_full(ctx.conn, task_id)

    @app.post("/api/tasks", status_code=201)
    def create_task(data: TaskCreate, ctx: Ctx = Depends(get_ctx)):
        conn = ctx.conn
        ensure_user_exists(conn, data.assignee_id)
        validate_parent(conn, None, data.parent_id)
        cur = conn.execute(
            """INSERT INTO tasks (title, description, status, priority, category, assignee_id,
                                  creator_id, due_date, recurrence, points, position, completed_at,
                                  parent_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.title.strip(), data.description, data.status, data.priority,
             data.category.strip(), data.assignee_id, ctx.user["id"],
             data.due_date.isoformat() if data.due_date else None, data.recurrence, data.points,
             next_position(conn, "tasks", "status", data.status),
             now_iso() if data.status == "done" else None, data.parent_id),
        )
        task_id = cur.lastrowid
        add_checklist(conn, task_id, data.checklist)
        ctx.log(task_id, "created")
        task = get_task_row(conn, task_id)
        if task["assignee_id"]:
            notify_assigned(ctx, task)
        return task_full(conn, task_id)

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: int, data: TaskUpdate, ctx: Ctx = Depends(get_ctx)):
        conn = ctx.conn
        task = get_task_row(conn, task_id)
        changes = data.model_dump(exclude_unset=True)
        for required in ("title", "description", "status", "priority", "category", "recurrence", "points"):
            if required in changes and changes[required] is None:
                raise HTTPException(422, f"Поле {required} не может быть пустым")
        if "assignee_id" in changes:
            ensure_user_exists(conn, changes["assignee_id"])
        if changes.get("due_date") is not None:
            changes["due_date"] = changes["due_date"].isoformat()
        for key in ("title", "category"):
            if key in changes:
                changes[key] = changes[key].strip()
        # В базу и историю попадает только то, что действительно поменялось.
        changes = {k: v for k, v in changes.items() if task[k] != v}
        if "parent_id" in changes:
            validate_parent(conn, task_id, changes["parent_id"])

        new_status = changes.pop("status", task["status"])
        if changes:
            cols = ", ".join(f"{k} = ?" for k in changes)
            conn.execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*changes.values(), task_id))
        if "assignee_id" in changes:
            ctx.log(task_id, "assigned", **{"from": task["assignee_id"], "to": changes["assignee_id"]})
            if changes["assignee_id"]:
                notify_assigned(ctx, get_task_row(conn, task_id))
        edited = [k for k in EDITABLE_FIELDS if k in changes]
        if edited:
            ctx.log(task_id, "edited", fields=edited)
        if new_status != task["status"]:
            conn.execute(
                "UPDATE tasks SET status = ?, position = ? WHERE id = ?",
                (new_status, next_position(conn, "tasks", "status", new_status), task_id),
            )
            change_status(ctx, task_id, task["status"], new_status)
        if changes or new_status != task["status"]:
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_iso(), task_id))
        return task_full(conn, task_id)

    @app.post("/api/tasks/{task_id}/move")
    def move_task(task_id: int, data: MoveIn, ctx: Ctx = Depends(get_ctx)):
        conn = ctx.conn
        task = get_task_row(conn, task_id)
        ids = column_ids(conn, data.status, exclude=task_id)
        ids.insert(min(data.index, len(ids)), task_id)
        conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (data.status, task_id))
        write_order(conn, ids)
        if data.status != task["status"]:
            write_order(conn, column_ids(conn, task["status"], exclude=task_id))
            conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (now_iso(), task_id))
            change_status(ctx, task_id, task["status"], data.status)
        return task_full(conn, task_id)

    @app.delete("/api/tasks/{task_id}", status_code=204)
    def delete_task(task_id: int, ctx: Ctx = Depends(get_ctx)):
        if ctx.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount == 0:
            raise HTTPException(404, "Задача не найдена")

    # --- связи между задачами ---

    @app.post("/api/tasks/{task_id}/links", status_code=201)
    def add_link(task_id: int, data: LinkIn, ctx: Ctx = Depends(get_ctx)):
        conn = ctx.conn
        get_task_row(conn, task_id)
        if data.task_id == task_id:
            raise HTTPException(400, "Задачу нельзя связать с самой собой")
        if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (data.task_id,)).fetchone() is None:
            raise HTTPException(400, "Такой задачи нет")

        if data.kind == "relates":
            kind, frm, to = "relates", min(task_id, data.task_id), max(task_id, data.task_id)
        elif data.kind == "blocks":
            kind, frm, to = "blocks", task_id, data.task_id
        else:
            kind, frm, to = "blocks", data.task_id, task_id
        if kind == "blocks" and blocking_creates_cycle(conn, frm, to):
            raise HTTPException(400, "Получится цикл: задачи будут ждать друг друга")
        try:
            conn.execute("INSERT INTO task_links (kind, from_id, to_id) VALUES (?, ?, ?)", (kind, frm, to))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Такая связь уже есть")

        ctx.log(task_id, "linked", type=data.kind, other=data.task_id)
        ctx.log(data.task_id, "linked", type=INVERSE_LINK[data.kind], other=task_id)
        return task_full(conn, task_id)

    @app.delete("/api/links/{link_id}", status_code=204)
    def delete_link(link_id: int, ctx: Ctx = Depends(get_ctx)):
        link = ctx.conn.execute("SELECT * FROM task_links WHERE id = ?", (link_id,)).fetchone()
        if link is None:
            raise HTTPException(404, "Связь не найдена")
        ctx.conn.execute("DELETE FROM task_links WHERE id = ?", (link_id,))
        ctx.log(link["from_id"], "unlinked", type=link["kind"], other=link["to_id"])
        ctx.log(link["to_id"], "unlinked", type=INVERSE_LINK[link["kind"]], other=link["from_id"])

    # --- чек-листы ---

    def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Пункт не найден")
        return row

    @app.post("/api/tasks/{task_id}/checklist", status_code=201)
    def add_item(task_id: int, data: TextIn, ctx: Ctx = Depends(get_ctx)):
        get_task_row(ctx.conn, task_id)
        if len(data.text) > 200:
            raise HTTPException(422, "Пункт длиннее 200 символов")
        add_checklist(ctx.conn, task_id, [data.text])
        row = ctx.conn.execute(
            "SELECT * FROM checklist_items WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)
        ).fetchone()
        return item_out(row)

    @app.patch("/api/checklist/{item_id}")
    def update_item(item_id: int, data: ChecklistUpdate, ctx: Ctx = Depends(get_ctx)):
        get_item(ctx.conn, item_id)
        if data.text is not None:
            ctx.conn.execute("UPDATE checklist_items SET text = ? WHERE id = ?", (data.text.strip(), item_id))
        if data.done is not None:
            ctx.conn.execute("UPDATE checklist_items SET done = ? WHERE id = ?", (int(data.done), item_id))
        return item_out(get_item(ctx.conn, item_id))

    @app.delete("/api/checklist/{item_id}", status_code=204)
    def delete_item(item_id: int, ctx: Ctx = Depends(get_ctx)):
        get_item(ctx.conn, item_id)
        ctx.conn.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))

    # --- история и комментарии ---

    @app.get("/api/tasks/{task_id}/events")
    def task_events(task_id: int, ctx: Ctx = Depends(get_ctx)):
        get_task_row(ctx.conn, task_id)
        rows = ctx.conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY id", (task_id,)
        ).fetchall()
        return [event_out(r) for r in rows]

    @app.post("/api/tasks/{task_id}/comments", status_code=201)
    def add_comment(task_id: int, data: TextIn, ctx: Ctx = Depends(get_ctx)):
        task = get_task_row(ctx.conn, task_id)
        event_id = ctx.log(task_id, "comment", text=data.text)
        ctx.notify(
            [task["assignee_id"], task["creator_id"]],
            f"💬 {ctx.actor} о задаче {quoted(task)}:\n{escape(data.text)}",
        )
        return event_out(ctx.conn.execute("SELECT * FROM task_events WHERE id = ?", (event_id,)).fetchone())

    @app.delete("/api/events/{event_id}", status_code=204)
    def delete_comment(event_id: int, ctx: Ctx = Depends(get_ctx)):
        row = ctx.conn.execute("SELECT * FROM task_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Запись не найдена")
        if row["kind"] != "comment":
            raise HTTPException(400, "Удалять можно только комментарии")
        if row["user_id"] != ctx.user["id"] and not ctx.user["is_admin"]:
            raise HTTPException(403, "Можно удалить только свой комментарий")
        ctx.conn.execute("DELETE FROM task_events WHERE id = ?", (event_id,))

    # --- справочники и статистика ---

    @app.get("/api/categories")
    def categories(ctx: Ctx = Depends(get_ctx)):
        rows = ctx.conn.execute(
            "SELECT DISTINCT category FROM tasks WHERE category != '' ORDER BY category COLLATE NOCASE"
        ).fetchall()
        return [r["category"] for r in rows]

    @app.get("/api/stats")
    def stats(ctx: Ctx = Depends(get_ctx)):
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        rows = ctx.conn.execute(
            """SELECT u.id AS user_id,
                      SUM(t.status != 'done') AS open,
                      SUM(t.status = 'done' AND t.completed_at >= ?) AS done_week
               FROM users u LEFT JOIN tasks t ON t.assignee_id = u.id
               GROUP BY u.id""",
            (since,),
        ).fetchall()
        return [{"user_id": r["user_id"], "open": r["open"] or 0, "done_week": r["done_week"] or 0}
                for r in rows]

    @app.get("/api/leaderboard")
    def leaderboard(period: Literal["week", "month", "all"] = "week", ctx: Ctx = Depends(get_ctx)):
        rows = ctx.conn.execute(
            """SELECT u.id AS user_id, COALESCE(SUM(t.points), 0) AS points, COUNT(t.id) AS done
               FROM users u
               LEFT JOIN tasks t ON t.assignee_id = u.id AND t.status = 'done' AND t.completed_at >= ?
               GROUP BY u.id
               ORDER BY points DESC, done DESC, u.display_name COLLATE NOCASE""",
            (period_start(period),),
        ).fetchall()
        return [dict(r) for r in rows]

    # Service worker отдаётся с версией интерфейса внутри: новый релиз — новый байт-код
    # sw.js, и браузер сам ставит обновление. Кешировать сам sw.js нельзя.
    service_worker = (STATIC_DIR / "sw.js").read_text(encoding="utf-8").replace(
        "__VERSION__", static_version(STATIC_DIR)
    )

    @app.get("/sw.js", include_in_schema=False)
    def sw():
        return PlainTextResponse(
            service_worker, media_type="text/javascript", headers={"Cache-Control": "no-cache"}
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
