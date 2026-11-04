# Наблюдатели и уведомления на почту — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Наблюдатели задач («глазик») и одинаковые уведомления в Telegram и на почту обо всём, что происходит с задачей, с группировкой мелких правок.

**Architecture:** Действия над задачей записывают изменения в очередь `outbox` в SQLite — по строке на получателя и канал. Фоновый `Dispatcher` раз в несколько секунд собирает созревшие строки по (получатель, канал, задача), склеивает их в одно сообщение на языке получателя и отправляет через канал (`TelegramChannel`, `EmailChannel`); неудачи повторяются с растущей паузой до 24 часов. Почта — `smtplib` из стандартной библиотеки.

**Tech Stack:** Python 3.13, FastAPI, sqlite3, smtplib/email (stdlib), vanilla JS, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-watchers-and-email-design.md`

## Global Constraints

- Новых зависимостей во время работы приложения нет; почта — `smtplib`, `ssl`, `email` из stdlib.
- Миграция базы — номер 9 в `app/db.py:MIGRATIONS`, повторный запуск ничего не дублирует.
- Окно группировки `GROUP_SECONDS = 120`. «Сразу»: `comment`, `assigned`, `moved`, `unblocked`, `deleted`.
- Повторы отправки: пауза `min(30 * 2**attempts, 3600)` секунд; строка старше 24 часов снимается с записью `log.warning`.
- Письмо подтверждения: ссылка живёт 24 часа; не чаще 1 письма в минуту и 5 в сутки на человека.
- SMTP: `SMTP_HOST` (без него почта выключена), `SMTP_PORT` = 587, `SMTP_SECURITY` = `starttls` | `ssl` | `none` (по умолчанию `starttls`), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (обязателен при `SMTP_HOST`), `SMTP_CA_FILE`. Проверка сертификата не отключается.
- Час сводки: `DIGEST_HOUR`, иначе `TELEGRAM_DIGEST_HOUR`, иначе 9.
- Адрес почты не попадает в `user_out()` и `GET /api/users`; его видит только владелец через `GET /api/me`.
- Все новые строки — русский ключ и английский перевод (`app/i18n.py`, `app/static/i18n.js`); существующие тесты полноты переводов проходят.
- Тексты уже существующих уведомлений для одиночного события не меняются (на них есть тесты в `tests/test_i18n.py`).
- Ссылка на задачу: `{TODO_PUBLIC_URL}/#task={id}`; без `TODO_PUBLIC_URL` ссылок нет.
- Версия 0.9.0.

## Review Focus

1. Задачу удалили, пока по ней копились правки → получатели получают одно сообщение «удалена» вместе с накопленным, с названием из снимка; ничего не падает на отсутствующей задаче. Тест в Task 3.
2. Telegram доставил, SMTP недоступен → письмо повторяется по расписанию, Telegram повторно не отправляется. Тест в Task 4.
3. Человек выключил почту или удалил адрес, пока письма в очереди → эти строки снимаются без отправки. Тест в Task 5.
4. Название задачи с переводом строки и `<script>` → тема письма в одну строку, HTML экранирован в обоих каналах. Тест в Task 4.
5. Ссылка `#task=ID` на удалённую задачу или открытая без входа → после входа сообщение «Задача не найдена», доска работает. Проверка в Task 7.

---

### Task 1: Наблюдатели — таблица, API, автоподписка

**Files:**
- Modify: `app/db.py` (миграция 9), `app/main.py` (маршруты, `task_full`, создание, назначение, комментарий, `spawn_next`)
- Create: `app/notify.py` (пока только функции наблюдателей)
- Test: `tests/test_watchers.py`

**Interfaces:**
- Produces (`app/notify.py`): `watch(conn, task_id: int, user_id: int) -> None` (INSERT OR IGNORE), `unwatch(conn, task_id: int, user_id: int) -> None`, `watcher_ids(conn, task_id: int) -> list[int]`.
- Produces (API): `POST /api/tasks/{id}/watch` и `DELETE /api/tasks/{id}/watch` → `task_full`; в `task_full` поле `watchers: list[int]` (id людей, по возрастанию).
- Migration 9 also creates (used by later tasks): `outbox` с колонками из `record()` в Task 2 (без внешнего ключа на `tasks`: строки переживают удаление задачи), колонки `users.email`, `users.email_pending`, `users.email_token_hash`, `users.email_token_expires`, `users.email_notify INTEGER NOT NULL DEFAULT 1`, `users.email_digest INTEGER NOT NULL DEFAULT 0`, таблица `email_confirm_log(user_id, sent_at)`.

- [ ] **Step 1: Write the failing tests** (`tests/test_watchers.py`, фикстуры `admin`, второй пользователь как в `tests/helpers.py`)
  - `test_creator_watches_new_task`: после `POST /api/tasks` → `watchers == [creator_id]`.
  - `test_assignee_starts_watching_and_previous_stays`: назначить A, потом B → `watchers` содержит создателя, A и B.
  - `test_commenter_starts_watching`: комментарий от третьего → он в `watchers`.
  - `test_eye_toggles_only_self`: `POST /watch` добавляет себя, `DELETE /watch` убирает; повторный `POST` не дублирует.
  - `test_unwatched_returns_only_on_next_trigger`: снял глазик, правка поля его не возвращает, комментарий возвращает.
  - `test_repeat_copy_inherits_watchers`: повторяющаяся задача в «Готово» → новая копия с теми же `watchers`.
  - `test_deleted_user_leaves_watchers`: удалить пользователя → его нет в `watchers`.
  - `test_migration_backfills_watchers`: база в версии 8 с задачей (автор, исполнитель) и комментарием третьего → после `init_db` все трое наблюдают; второй `init_db` ничего не меняет.
- [ ] **Step 2: Run, verify they fail** — `.venv/bin/python -m pytest tests/test_watchers.py -q` → FAIL (нет маршрутов и поля `watchers`).
- [ ] **Step 3: Implement.** Миграция 9: `task_watchers(task_id REFERENCES tasks ON DELETE CASCADE, user_id REFERENCES users ON DELETE CASCADE, PRIMARY KEY(task_id, user_id))`, заполнение из `tasks.creator_id`, `tasks.assignee_id`, `task_events` с `kind='comment'`; остальные объекты из Interfaces. Автоподписка: `create_task` (создатель и исполнитель), назначение в `update_task`, `add_comment`, `spawn_next` копирует наблюдателей.
- [ ] **Step 4: Run** — `pytest tests/test_watchers.py -q` и весь `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `Наблюдатели задач: таблица, глазик в API, автоподписка`.

### Task 2: Очередь и отправка в Telegram для «сразу»-событий

**Files:**
- Modify: `app/notify.py`, `app/main.py` (`Ctx`, `create_app`, `notify_assigned`, `change_status`, `notify_unblocked`, `add_comment`, `lifespan`), `app/telegram.py`, `app/i18n.py`
- Create: `app/outbox.py`
- Modify tests: `tests/conftest.py`, места с `notifier.sent` в `tests/test_i18n.py`, `test_relations.py`, `test_boards.py`, `test_features.py`
- Test: `tests/test_notifications.py`

**Interfaces:**
- Consumes: `watcher_ids` из Task 1.
- Produces (`app/notify.py`):
  - `GROUP_SECONDS = 120`, `IMMEDIATE = frozenset({"comment", "assigned", "moved", "unblocked", "deleted"})`.
  - `record(conn, task: Mapping, actor: Mapping, change: dict, now: datetime, channels: Sequence[Channel]) -> None` — для каждого наблюдателя кроме `actor["id"]` и каждого канала, где `channel.address(user)` не `None`, вставляет строку `outbox(user_id, channel, task_id, task_title, actor_name, kind, payload JSON, created_at, due_at, attempts=0, next_try_at NULL)`; `due_at = now` для `IMMEDIATE`, иначе `now + GROUP_SECONDS`. `change = {"kind": ..., **params}`.
  - `@dataclass Message(task_id: int | None, subject: str, lines: list[str], text_lines: list[str])` — `lines` экранированы для HTML, `text_lines` — обычный текст.
  - `render(rows: list[sqlite3.Row], lang: str) -> Message | None` — `None`, если после сокращений нечего сказать.
- Produces (`app/outbox.py`):
  - `class Channel(Protocol)`: `name: str`; `address(user: sqlite3.Row) -> str | None`; `deliver(address: str, message: Message, link: str | None) -> None` (исключение = неудача).
  - `class TelegramChannel(notifier)`: `name = "telegram"`, адрес — `telegram_chat_id`, `deliver` вызывает `notifier.send_now(chat_id, text)`; текст — `"\n".join(message.lines)` и `"\n\n" + link`, если ссылка есть.
  - `class Dispatcher(db_path: str, channels: list[Channel], public_url: str | None, clock: Callable[[], datetime])`: `deliver_due(now: datetime | None = None) -> int`, `start()`, `stop()`; поток проверяет очередь каждые 5 секунд.
- Produces (`app/main.py`): `create_app(..., clock: Callable[[], datetime] | None = None)`; `app.state.dispatcher`; `Ctx.record(task, change)` вместо `Ctx.notify`.
- Produces (`app/telegram.py`): `TelegramBot.send_now(chat_id, text) -> None` — синхронный `sendMessage`, бросает исключение; `send()` остаётся для ответов бота.
- Produces (tests): в `conftest.py` фикстура `clock` (изменяемое время, UTC) и `deliver(app, seconds: int = 0) -> None`, вызывающая `app.state.dispatcher.deliver_due(clock.now + timedelta(seconds=seconds))`; `FakeNotifier.send_now` пишет в `sent`.

- [ ] **Step 1: Write the failing tests** (`tests/test_notifications.py`)
  - `test_watchers_get_comment_except_author`: автор, исполнитель и наблюдатель; комментирует наблюдатель → после `deliver` сообщение получили автор и исполнитель, наблюдатель — нет.
  - `test_move_reaches_all_watchers`: перенос в «В работе» → все наблюдатели кроме того, кто переносит, получили сообщение с названиями обеих колонок.
  - `test_review_and_done_texts_unchanged`: одиночные события дают те же строки, что сейчас проверяет `tests/test_i18n.py`.
  - `test_unblocked_goes_to_watchers_of_waiting_task`.
  - `test_nothing_sent_before_deliver`: до `deliver` `notifier.sent == []` — отправка только из очереди.
  - `test_failed_telegram_is_retried_with_backoff`: `send_now` бросает один раз → сообщение уходит при `deliver(app, 30)`, а не раньше.
  - `test_row_older_than_day_is_dropped`: всегда бросает → после `deliver(app, 24*3600 + 1)` очередь пуста, есть предупреждение в журнале.
  - `test_link_added_when_public_url_set`: с `TODO_PUBLIC_URL=https://b.example` текст оканчивается на `https://b.example/#task={id}`.
- [ ] **Step 2: Run, verify they fail** — `pytest tests/test_notifications.py -q` → FAIL.
- [ ] **Step 3: Implement.** Перевести `notify_assigned`, перенос (`change_status`: `moved` с `from_title`, `to_title`, `to_role`), `notify_unblocked`, `add_comment` на `ctx.record`. Получатели — наблюдатели (для «можно начинать» — наблюдатели ожидавшей задачи). Новому исполнителю — прежний текст «поручает вам», остальным — «исполнитель: А → Б». Выбор строк в `deliver_due`: группы `(user_id, channel, task_id)` с `min(due_at) <= now` и `next_try_at` пуст или наступил; в группу входят все её строки с `created_at <= now`; успех — удалить строки; неудача — `attempts + 1` и `next_try_at`; при отправке перепроверить, что человек существует и `channel.address(user)` не `None`, иначе удалить без отправки. Перевести существующие тесты с `notifier.sent` на вызов `deliver(app)` перед проверкой.
- [ ] **Step 4: Run** — весь `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `Уведомления наблюдателям через очередь в базе`.

### Task 3: Правки, чек-лист, связи, подзадачи, удаление — с группировкой

**Files:**
- Modify: `app/main.py` (`update_task`, `delete_task`, чек-лист, связи, `validate_parent`-места), `app/notify.py` (`render`), `app/i18n.py`
- Test: `tests/test_notifications.py`

**Interfaces:**
- Consumes: `record`, `render`, `deliver` из Task 2.
- Produces kinds: `field` (`field`, `old`, `new`; для `description` без значений), `checklist` (`action`: `added|checked|unchecked|renamed|removed`, `text`, `old_text`), `link` (`action`: `added|removed`, `type`, `other_title`), `subtask` (`action`: `added|removed`, `title`), `deleted`.

- [ ] **Step 1: Write the failing tests**
  - `test_one_save_one_message`: один `PATCH` меняет срок и приоритет → после `deliver(app, 120)` одно сообщение с обоими полями «было → стало».
  - `test_edits_wait_for_window`: правка срока → `deliver(app, 119)` ничего не шлёт, `deliver(app, 120)` шлёт.
  - `test_checklist_clicks_grouped`: пять отметок за минуту → одно сообщение с пятью строками.
  - `test_immediate_event_carries_pending_edits`: правка поля, затем комментарий → одно сообщение сразу, в нём и правка, и комментарий.
  - `test_change_reverted_inside_window_is_omitted`: приоритет `normal → high → normal` → сообщения нет вовсе.
  - `test_description_change_has_no_text`: в сообщении «описание изменено», текста описания нет.
  - `test_delete_flushes_pending_with_snapshot_title` (Review Focus 1): правка, затем удаление → одно сообщение с названием задачи, «удалена» и правкой; `deliver` не падает.
  - `test_subtask_and_link_changes_reach_parent_watchers`.
  - `test_two_actors_in_one_group_are_named`: правки от двух людей → каждая строка с именем автора правки.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement.** В `update_task` сравнивать со старой строкой задачи и записывать `field` по каждому изменённому полю из `EDITABLE_FIELDS` (кроме `parent_id` → `subtask` у старого и нового родителя). `delete_task` записывает `deleted` до удаления. Сокращение в `render`: по каждому полю сравнить первое `old` и последнее `new` в группе.
- [ ] **Step 4: Run** — весь `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `Уведомления о правках, чек-листе и связях с группировкой`.

### Task 4: Почта — отправка и канал

**Files:**
- Create: `app/mailer.py`
- Modify: `app/outbox.py` (`EmailChannel`), `app/main.py` (создание каналов), `app/i18n.py`
- Test: `tests/test_mailer.py`

**Interfaces:**
- Produces (`app/mailer.py`): `@dataclass SmtpConfig(host: str, sender: str, port: int = 587, security: str = "starttls", user: str | None = None, password: str | None = None, ca_file: str | None = None)`; `SmtpConfig.from_env() -> SmtpConfig | None` (`None` без `SMTP_HOST`, `ValueError` без `SMTP_FROM` или при неизвестном `SMTP_SECURITY`); `class Mailer(config, smtp_factory=None)`: `send(to: str, subject: str, text: str, html: str | None = None, thread: str | None = None) -> None`.
- Produces (`app/outbox.py`): `EmailChannel(mailer, site_title: Callable[[], str])`: `name = "email"`; адрес — `users.email`, если `email_notify = 1`; тема `[{site_title}] {task_title}`; текстовая и HTML-части; кнопка «Открыть задачу» и ссылки «Перестать наблюдать», «Настройки уведомлений» (`{public_url}/#profile`), если есть ссылка; `thread = f"task-{task_id}"`.

- [ ] **Step 1: Write the failing tests** (фейковый SMTP через `smtp_factory`)
  - `test_from_env`: без `SMTP_HOST` → `None`; без `SMTP_FROM` → `ValueError`; `SMTP_SECURITY=tls` → `ValueError`; значения по умолчанию 587 и `starttls`.
  - `test_starttls_ssl_none_paths`: для каждого режима вызываются ожидаемые методы фейка; при `starttls` и `ssl` используется контекст с проверкой сертификата, `ca_file` загружается.
  - `test_thread_headers`: два письма по одной задаче → одинаковый `References`, `In-Reply-To` указывает на корень цепочки.
  - `test_subject_single_line_and_html_escaped` (Review Focus 4): название `"A\nB <script>"` → в теме нет `\n`, в HTML `&lt;script&gt;`; в Telegram-тексте тоже экранировано.
  - `test_email_failure_does_not_resend_telegram` (Review Focus 2): Telegram успешен, SMTP бросает → после следующего `deliver` Telegram получил одно сообщение, письмо ушло со второй попытки.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement.** `ssl.create_default_context(cafile=ca_file)`; таймаут соединения 15 секунд; `email.message.EmailMessage`, `make_msgid` с доменом из `SMTP_FROM`; перевод строки в теме заменяется пробелом.
- [ ] **Step 4: Run** — весь `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `Отправка уведомлений на почту через SMTP`.

### Task 5: Адрес почты — ввод, подтверждение, настройки

**Files:**
- Modify: `app/main.py` (маршруты, `me`), `app/i18n.py`
- Test: `tests/test_email_address.py`

**Interfaces:**
- Consumes: `Mailer` из Task 4.
- Produces (API): `GET /api/me` → `user_out` плюс `email`, `email_pending`, `email_notify`, `email_digest`; `GET /api/config` плюс `email: bool`. `PUT /api/me/email {email}` → 202, письмо подтверждения; 429 при превышении частоты; 502, если письмо не ушло. `DELETE /api/me/email` → 204. `GET /api/email/confirm?token=` без входа → 303 на `/?email=confirmed` или `/?email=invalid`. `PATCH /api/me/notifications {email_notify?, email_digest?}`.

- [ ] **Step 1: Write the failing tests**
  - `test_confirmation_flow`: `PUT` → письмо на новый адрес с ссылкой; до перехода `email` пуст и уведомления на почту не создаются; после перехода `email` установлен.
  - `test_link_expires_after_24h` и `test_token_single_use`.
  - `test_change_keeps_old_until_confirmed`.
  - `test_rate_limits`: второй `PUT` в ту же минуту → 429; шестой за сутки → 429.
  - `test_email_hidden_from_others`: `GET /api/users` и `user_out` без адреса; администратор не видит чужой адрес.
  - `test_admin_cannot_set_email_for_others`: в `PATCH /api/users/{id}` поле `email` игнорируется или даёт 422.
  - `test_disabled_or_removed_email_drops_queue` (Review Focus 3): письма в очереди, человек выключил почту → после `deliver` писем нет.
  - `test_email_off_without_smtp`: без `SMTP_HOST` `config.email` ложно, `PUT` → 400.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement.** Токен из `security.new_token()`, в базе только его SHA-256; проверка адреса — одна `@`, без пробелов и переводов строки, не длиннее 254 символов.
- [ ] **Step 4: Run** — весь `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `Адрес почты с подтверждением и настройки уведомлений`.

### Task 6: Утренняя сводка в обоих каналах

**Files:**
- Modify: `app/telegram.py` (`build_digest` → тексты по людям, планировщик уходит в `Dispatcher`), `app/outbox.py`, `app/main.py`
- Test: `tests/test_digest.py` (и существующие тесты сводки)

**Interfaces:**
- Produces: `build_digest(conn, today: date) -> list[tuple[int, Message]]` (id человека, сообщение); `Dispatcher.run_digest(now: datetime) -> None`: в час `digest_hour` раз в день кладёт сводку в очередь как «сразу» для Telegram (если привязан) и почты (если подтверждена и `email_digest = 1`). `digest_hour` из `DIGEST_HOUR`, иначе `TELEGRAM_DIGEST_HOUR`, иначе 9.

- [ ] **Step 1: Write the failing tests** — `test_digest_once_per_day_both_channels`, `test_email_digest_off_by_default`, `test_digest_hour_env_fallback`.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement**, сохранив ключ `settings.last_digest`.
- [ ] **Step 4: Run** — весь `pytest -q` → PASS.
- [ ] **Step 5: Commit** — `Утренняя сводка в Telegram и на почту`.

### Task 7: Интерфейс

**Files:**
- Modify: `app/static/app.js`, `app/static/index.html`, `app/static/style.css`, `app/static/i18n.js`
- Test: `tests/test_frontend_i18n.py` (полнота переводов); ручная проверка через Playwright

**Interfaces:**
- Consumes: API из Tasks 1 и 5.

- [ ] **Step 1:** Глазик в заголовке окна задачи: состояние «наблюдаю/не наблюдаю», число наблюдателей, имена в `title`; переключение вызывает `POST`/`DELETE /watch`.
- [ ] **Step 2:** Открытие `#task=ID` после загрузки и после входа; хеш убирается после открытия; нет задачи → сообщение «Задача не найдена» (Review Focus 5). `#profile` открывает профиль.
- [ ] **Step 3:** В профиле рядом с Telegram — блок «Почта»: поле адреса, кнопка «Подтвердить», статус «ждёт подтверждения: адрес», переключатели «Уведомления на почту» и «Утренняя сводка на почту»; блока нет, если `config.email` ложно. Сообщение после `?email=confirmed|invalid`.
- [ ] **Step 4: Run** — `pytest -q` → PASS; Playwright-скрипт в scratchpad: глазик, ссылка `#task`, подтверждение адреса на локальном сервере с фейковым SMTP; скриншоты светлой и тёмной темы, телефонная ширина.
- [ ] **Step 5: Commit** — `Глазик, ссылки на задачи и почта в профиле`.

### Task 8: Настройки, документация, выпуск

**Files:**
- Modify: `.env.example`, `compose.yaml` (ничего, кроме комментария, если нужно), `charts/to-do-board/values.yaml`, `values.schema.json`, `templates/deployment.yaml` (`config.smtp.*`, `caSecret` как у LDAP, пароль через `vault.env` как `SMTP_PASSWORD`), `README.md`, `README.ru.md`, `CHANGELOG.md` (`## [0.9.0]` и ссылка сравнения)

- [ ] **Step 1:** Настройки и документация; `helm lint` и `helm template` с включённым SMTP.
- [ ] **Step 2:** Миграция на копии рабочей базы с сервера (`docker cp`, только чтение оригинала): `user_version` 9, наблюдатели заполнены, данные на месте.
- [ ] **Step 3:** Весь `pytest -q`, линтеры как в CI.
- [ ] **Step 4: Commit** — `Версия 0.9.0: наблюдатели и почта`; публикация и тег `v0.9.0` — только после подтверждения пользователя.
