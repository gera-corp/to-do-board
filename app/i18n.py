"""Переводы серверных текстов: ошибки API, уведомления и бот.

Исходные строки — на русском, они же ключи; здесь лежит английский перевод.
Язык по умолчанию — английский.
"""

LANGS = ("en", "ru")
DEFAULT_LANG = "en"

EN = {
    # --- ошибки API ---
    "Google-почту назначает администратор": "Only an administrator can assign a Google address",
    "Telegram-бот не настроен или ещё не подключился": "The Telegram bot is not configured or has not connected yet",
    "Вход по паролю отключён. Войдите через Google.": "Password sign-in is turned off. Sign in with Google.",
    "Вход через Google не настроен": "Sign-in with Google is not configured",
    "Задача не может быть подзадачей самой себя": "A task cannot be its own subtask",
    "Задача не найдена": "Task not found",
    "Задачу нельзя связать с самой собой": "A task cannot be linked to itself",
    "Запись не найдена": "Entry not found",
    "Можно менять только свой профиль": "You can only change your own profile",
    "Можно удалить только свой комментарий": "You can only delete your own comment",
    "Неверный логин или пароль": "Wrong username or password",
    "Нельзя снять права администратора с себя": "You cannot remove administrator rights from yourself",
    "Нельзя удалить самого себя": "You cannot delete yourself",
    "Нужно войти": "Please sign in",
    "Первый пользователь уже создан": "The first user has already been created",
    "Подзадачи бывают только одного уровня: выбрана сама подзадача":
        "Subtasks are one level deep: the chosen task is itself a subtask",
    "Поле {field} не может быть пустым": "The {field} field cannot be empty",
    "Получится цикл: задачи будут ждать друг друга": "That would create a cycle: the tasks would wait for each other",
    "Пользователь не найден": "User not found",
    "Права администратора выдаёт администратор": "Only an administrator can grant administrator rights",
    "Пункт длиннее 200 символов": "The item is longer than 200 characters",
    "Пункт не найден": "Item not found",
    "Родительской задачи нет": "The parent task does not exist",
    "Связь не найдена": "Link not found",
    "Слишком много неудачных попыток. Попробуйте через 15 минут.": "Too many failed attempts. Try again in 15 minutes.",
    "Такая связь уже есть": "This link already exists",
    "Такого пользователя нет": "No such user",
    "Такой задачи нет": "No such task",
    "Такой логин уже занят": "This username is already taken",
    "У задачи есть свои подзадачи, её нельзя сделать подзадачей": "The task has subtasks of its own, so it cannot become a subtask",
    "Удалять можно только комментарии": "Only comments can be deleted",
    "Эта Google-почта уже привязана к другому человеку": "This Google address is already assigned to someone else",
    "Это может сделать только администратор": "Only an administrator can do this",

    "Родительская задача на другой доске": "The parent task is on another board",
    "На этой доске нет колонки с такой ролью": "This board has no column with that role",
    "На этой доске нет такой колонки": "This board has no such column",
    "Доска не найдена": "Board not found",
    "Колонка не найдена": "Column not found",
    "На доске должна остаться хотя бы одна начальная колонка и одна «Готово»":
        "A board must keep at least one start column and one Done column",
    "Нельзя удалить последнюю доску": "You cannot delete the last board",
    "На доске есть задачи": "The board has tasks",
    "Порядок должен перечислять все колонки доски": "The order must list every column of the board",
    "Сначала перенесите задачи из этой колонки": "Move the tasks out of this column first",
    "В колонке есть задачи: выберите, куда их перенести на этой доске":
        "The column has tasks: choose where to move them on this board",
    "Задачи можно перенести только в колонку с той же завершённостью":
        "Tasks can only move to a column that is equally finished",
    "Связывать можно только задачи одной доски": "Only tasks on the same board can be linked",

    # --- уведомления ---
    "📌 {actor} поручает вам: {task}": "📌 {actor} assigned you: {task}",
    "Срок: {date}": "Due: {date}",
    "👀 {actor} просит проверить: {task}": "👀 {actor} asks you to review: {task}",
    "✅ Сделано: {task}": "✅ Done: {task}",
    ", +{n} {points}": ", +{n} {points}",
    "Отметил(а): {actor}": "Marked by: {actor}",
    "🔓 Можно начинать: {task}": "🔓 You can start: {task}",
    "Готово: {task}": "Finished: {task}",
    "💬 {actor} о задаче {task}:": "💬 {actor} on {task}:",

    # --- бот ---
    "Я присылаю уведомления с доски задач.": "I send notifications from the task board.",
    "/tasks — мои открытые задачи": "/tasks — my open tasks",
    "/stop — отключить уведомления": "/stop — turn notifications off",
    "Этот чат не привязан к доске. Откройте доску → «Люди» → свой профиль → «Подключить Telegram».":
        "This chat is not linked to the board. Open the board → People → your profile → Connect Telegram.",
    "Ссылка устарела. Получите новую в своём профиле на доске.": "This link has expired. Get a new one in your profile on the board.",
    "Привет, {name}! Теперь сюда будут приходить уведомления о ваших задачах.":
        "Hi, {name}! Notifications about your tasks will now arrive here.",
    "Уведомления отключены. Подключить снова можно в профиле на доске.":
        "Notifications are off. You can connect again in your profile on the board.",
    "Открытых задач нет. Отдыхайте 🙂": "No open tasks. Time to rest 🙂",
    "Ваши задачи:": "Your tasks:",
    "Моя доска": "My board",
    " — до {date}": " — due {date}",
    "☀️ Доброе утро! Вот что на сегодня.": "☀️ Good morning! Here is what's on for today.",
    "Сегодня": "Today",
    "Просрочено": "Overdue",
    "Завтра": "Tomorrow",
}

MONTHS = {
    "ru": ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def normalize_lang(value: str | None) -> str:
    """'ru-RU', 'ru' → 'ru'; всё остальное (в том числе пусто) → английский."""
    return "ru" if (value or "").strip().lower().startswith("ru") else DEFAULT_LANG


def tr(text: str, lang: str, **params) -> str:
    template = EN.get(text, text) if lang == "en" else text
    return template.format(**params) if params else template


def points_word(n: int, lang: str) -> str:
    if lang == "en":
        return "point" if n == 1 else "points"
    m = abs(n) % 100
    if 11 <= m <= 14:
        return "очков"
    return {1: "очко", 2: "очка", 3: "очка", 4: "очка"}.get(m % 10, "очков")
