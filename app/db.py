import sqlite3
from pathlib import Path

# Каждая миграция выполняется один раз; номер последней хранится в PRAGMA user_version.
MIGRATIONS = [
    # 1: пользователи, сессии, задачи
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name  TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        color         TEXT NOT NULL DEFAULT '#2F5BD3',
        is_admin      INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS sessions (
        token      TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
        id           INTEGER PRIMARY KEY,
        title        TEXT NOT NULL,
        description  TEXT NOT NULL DEFAULT '',
        status       TEXT NOT NULL DEFAULT 'todo'
                     CHECK (status IN ('todo', 'in_progress', 'review', 'done')),
        priority     TEXT NOT NULL DEFAULT 'normal'
                     CHECK (priority IN ('low', 'normal', 'high')),
        category     TEXT NOT NULL DEFAULT '',
        assignee_id  INTEGER REFERENCES users(id) ON DELETE SET NULL,
        creator_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        due_date     TEXT,
        recurrence   TEXT NOT NULL DEFAULT 'none'
                     CHECK (recurrence IN ('none', 'daily', 'weekly', 'monthly')),
        position     INTEGER NOT NULL DEFAULT 0,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
        completed_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, position);
    CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
    """,
    # 2: Telegram, очки, чек-листы, история и комментарии
    """
    ALTER TABLE users ADD COLUMN telegram_chat_id INTEGER;
    ALTER TABLE users ADD COLUMN telegram_link_code TEXT;
    ALTER TABLE tasks ADD COLUMN points INTEGER NOT NULL DEFAULT 1;

    CREATE TABLE checklist_items (
        id       INTEGER PRIMARY KEY,
        task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        text     TEXT NOT NULL,
        done     INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_checklist_task ON checklist_items(task_id, position);

    CREATE TABLE task_events (
        id         INTEGER PRIMARY KEY,
        task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
        kind       TEXT NOT NULL,
        payload    TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX idx_events_task ON task_events(task_id, id);

    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 3: тема оформления пользователя
    """
    ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'auto';
    """,
    # 4: вход через Google. Почту назначает администратор; при первом входе
    # запоминается постоянный идентификатор аккаунта (sub).
    """
    ALTER TABLE users ADD COLUMN google_email TEXT;
    ALTER TABLE users ADD COLUMN google_sub TEXT;
    CREATE UNIQUE INDEX idx_users_google_email ON users(google_email) WHERE google_email IS NOT NULL;
    CREATE UNIQUE INDEX idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL;

    CREATE TABLE oauth_states (
        state      TEXT PRIMARY KEY,
        nonce      TEXT NOT NULL,
        verifier   TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    """,
]


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# WAL быстрее, но требует общей памяти и не работает на сетевых ФС (NFS) — там нужен DELETE.
JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE"}


def init_db(db_path: str, journal_mode: str = "WAL") -> None:
    journal_mode = journal_mode.upper()
    if journal_mode not in JOURNAL_MODES:
        raise ValueError(f"journal_mode должен быть одним из {sorted(JOURNAL_MODES)}")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute(f"PRAGMA journal_mode = {journal_mode}")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {number}; COMMIT;")
    finally:
        conn.close()
