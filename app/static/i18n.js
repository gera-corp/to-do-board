"use strict";

// Язык интерфейса. Выбор хранится в профиле ("en", "ru" или "auto"); "auto" — язык
// браузера: русский для русского браузера, английский для всех остальных.
// Исходные строки интерфейса написаны по-русски и служат ключами; ниже — английский
// перевод. Строки с {name} — шаблоны для t("…", { name }).

const LANG_CHOICES = ["auto", "en", "ru"];

function browserLang() {
  const preferred = (navigator.languages && navigator.languages[0]) || navigator.language || "en";
  return preferred.toLowerCase().startsWith("ru") ? "ru" : "en";
}

function storedLangChoice() {
  try {
    const value = localStorage.getItem("lang");
    return LANG_CHOICES.includes(value) ? value : "auto";
  } catch {
    return "auto";
  }
}

function effectiveLang(choice) {
  return choice === "en" || choice === "ru" ? choice : browserLang();
}

const LANG = effectiveLang(storedLangChoice());
const LOCALE = LANG === "ru" ? "ru" : "en-GB";
document.documentElement.lang = LANG;

const EN = /*EN-BEGIN*/{
  "Доска задач": "Task Board",
  "Моя доска": "My board",
  "Доска": "Board",
  "Настройки": "Settings",
  "Открыть репозиторий на GitHub": "Open the repository on GitHub",
  "Название приложения": "App name",
  "Доски": "Boards",
  "Новая доска": "New board",
  "Название доски": "Board name",
  "Очки и рейтинг": "Points and leaderboard",
  "Колонки": "Columns",
  "Новая колонка": "New column",
  "Название колонки": "Column name",
  "Роль колонки": "Column role",
  "Начальная": "Start",
  "В работе": "In progress",
  "Проверка": "Review",
  "Готово": "Done",
  "Выше": "Move up",
  "Ниже": "Move down",
  "Удалить колонку": "Delete column",
  "Удалить колонку {column}?": "Delete the column {column}?",
  "Куда перенести задачи": "Where to move the tasks",
  "Задач в колонке: {n}. Перенести в:": "Tasks in this column: {n}. Move to:",
  "Сначала перенесите задачи из этой колонки": "Move the tasks out of this column first",
  "Удалить доску": "Delete board",
  "Удалить доску {board}?": "Delete the board {board}?",
  "Удалить доску {board} вместе с задачами ({n})? Это нельзя отменить.": "Delete the board {board} with its tasks ({n})? This can't be undone.",
  "Роль определяет поведение: в «Начальную» попадают новые задачи и повторы, из «Проверки» приходит уведомление автору, в «Готово» задача закрывается и начисляются очки.":
    "The role sets the behaviour: new tasks and repeats land in Start, Review notifies the author, Done closes the task and awards points.",
  "Название категории": "Category name",

  "Войти через Google": "Sign in with Google",
  "или по паролю": "or with a password",
  "Логин": "Username",
  "Как вас называть": "Your name",
  "Пароль": "Password",
  "Войти": "Sign in",
  "Создать и войти": "Create and sign in",
  "Войдите, чтобы увидеть доску.": "Sign in to see the board.",
  "Создайте первого пользователя — он станет администратором и сможет добавить остальных.":
    "Create the first user — they become the administrator and can add everyone else.",
  "Нет связи с сервером. Войти можно будет, когда появится интернет.":
    "No connection to the server. You can sign in once you're back online.",
  "Этот Google-аккаунт не допущен к доске. Попросите администратора указать вашу почту в «Люди».":
    "This Google account isn't allowed on the board. Ask an administrator to add your address under People.",
  "Вход через Google прервался. Попробуйте ещё раз.": "Signing in with Google was interrupted. Please try again.",
  "Google не подтвердил вход. Попробуйте ещё раз.": "Google didn't confirm the sign-in. Please try again.",
  "Вход через Google отменён.": "Signing in with Google was cancelled.",
  "Не удалось войти через Google.": "Couldn't sign in with Google.",

  "Фильтр по исполнителю": "Filter by person",
  "Найти задачу": "Find a task",
  "Поиск": "Search",
  "Фильтр по категории": "Filter by category",
  "Все": "All",
  "Все категории": "All categories",
  "Показать задачи: {name}": "Show tasks: {name}",
  "Новая задача": "New task",
  "Рейтинг": "Leaderboard",
  "Тема": "Theme",
  "Люди": "People",
  "Установить": "Install",
  "Выйти": "Sign out",
  "Нет связи. Показана сохранённая версия доски, менять задачи можно будет, когда появится интернет.":
    "No connection. This is the saved version of the board; you can change tasks once you're back online.",
  "Нет связи с сервером. Изменение не сохранено.": "No connection to the server. The change was not saved.",
  "Нужно войти": "Please sign in",
  "Ошибка {status}": "Error {status}",
  "Проверьте поля: {fields}": "Check the fields: {fields}",

  "Пока пусто — добавьте задачу": "Nothing here yet — add a task",
  "Перетащите сюда то, чем заняты": "Drag here what you're working on",
  "Сюда — то, что нужно проверить": "Put here what needs checking",
  "Здесь появятся сделанные дела": "Finished tasks will show up here",
  "Добавить в {column}": "Add to {column}",
  "Показать старые ({n})": "Show older ({n})",

  "вчера": "yesterday",
  "просрочено, {date}": "overdue, {date}",
  "сегодня": "today",
  "завтра": "tomorrow",
  "Срочно": "Urgent",
  "Чек-лист": "Checklist",
  "Подзадачи": "Subtasks",
  "Повторяется": "Repeats",
  "каждый день": "every day",
  "каждую неделю": "every week",
  "каждый месяц": "every month",
  "⏳ ждёт: {title}{more}": "⏳ waits for: {title}{more}",
  " и ещё {n}": " and {n} more",
  "{n} {points} за выполнение": "{n} {points} for completing",
  "Входит в задачу": "Part of",
  "Никто не назначен": "Nobody assigned",
  "кто-то": "someone",
  "{name}: +{n} {points}": "{name}: +{n} {points}",
  "следующий повтор добавлен в To Do": "next repeat added to To Do",

  "Не сделаны подзадачи: {list}.": "Unfinished subtasks: {list}.",
  "Задача ждёт: {list}.": "The task is waiting for: {list}.",
  "Всё равно перенести {task} в {column}?": "Move {task} to {column} anyway?",

  "Задача": "Task",
  "Закрыть": "Close",
  "Что сделать": "What to do",
  "Подробности": "Details",
  "Новый пункт": "New item",
  "Новый пункт чек-листа": "New checklist item",
  "Добавить": "Add",
  "Новая подзадача": "New subtask",
  "Связи": "Links",
  "Вид связи": "Link type",
  "Ждёт": "Waits for",
  "Блокирует": "Blocks",
  "Связана с": "Related to",
  "ждёт": "waits for",
  "блокирует": "blocks",
  "связана с": "related to",
  "Задача для связи": "Task to link",
  "Связать": "Link",
  "Выберите задачу": "Choose a task",
  "Убрать связь с {task}": "Remove the link to {task}",
  "Ни в какую, самостоятельная задача": "None — a standalone task",
  "У задачи есть свои подзадачи, поэтому сама она подзадачей быть не может":
    "This task has subtasks, so it can't be a subtask itself",
  "Кто делает": "Who does it",
  "Никто": "Nobody",
  "Колонка": "Column",
  "Срок": "Due date",
  "Повторять": "Repeat",
  "Не повторять": "Don't repeat",
  "Каждый день": "Every day",
  "Каждую неделю": "Every week",
  "Каждый месяц": "Every month",
  "Категория": "Category",
  "Очки за выполнение": "Points for completing",
  "1 — мелочь": "1 — a small thing",
  "3 — обычное дело": "3 — a regular job",
  "5 — заметная работа": "5 — real work",
  "8 — большое дело": "8 — a big job",
  "Важность": "Priority",
  "Можно потом": "Can wait",
  "Обычная": "Normal",
  "Удалить": "Delete",
  "Отмена": "Cancel",
  "Сохранить": "Save",
  "{ready} из {total}": "{ready} of {total}",
  "Удалить пункт {item}": "Delete item {item}",
  "Удалить задачу {task}?{warn}": "Delete the task {task}?{warn}",
  " Очки за неё пропадут из рейтинга.": " Its points will disappear from the leaderboard.",

  "История и комментарии": "History and comments",
  "Написать комментарий": "Write a comment",
  "Комментарий": "Comment",
  "Отправить": "Send",
  "удалить": "delete",
  "Удалить комментарий?": "Delete this comment?",
  "Удалённый пользователь": "Deleted user",
  "удалённая задача": "deleted task",
  "создаёт следующий повтор": "creates the next repeat",
  "создаёт задачу": "creates the task",
  "переносит из {from} в {to}": "moves it from {from} to {to}",
  "назначает исполнителем: {name}": "assigns it to {name}",
  "снимает исполнителя": "removes the assignee",
  "меняет {fields}": "changes {fields}",
  "связывает: {phrase} {task}": "links: {phrase} {task}",
  "убирает связь: {phrase} {task}": "removes the link: {phrase} {task}",
  "название": "title",
  "описание": "description",
  "важность": "priority",
  "категорию": "category",
  "срок": "due date",
  "повтор": "repeat",
  "очки": "points",
  "родительскую задачу": "parent task",

  "Неделя": "Week",
  "Месяц": "Month",
  "Всё время": "All time",
  "Выполнено задач: {n}": "Tasks done: {n}",
  "Очки начисляются исполнителю, когда задача попадает в Done. Если задачу вернуть из Done, очки снимаются.":
    "Points go to the assignee when a task reaches Done. Moving a task back out of Done takes them away.",

  "Оформление": "Appearance",
  "Язык": "Language",
  "Как в браузере": "Browser language",
  "Тема и язык сохраняются в вашем профиле и будут одинаковыми на всех устройствах.":
    "Theme and language are saved in your profile and follow you across devices.",
  "Тема не сохранилась: {error}": "The setting wasn't saved: {error}",
  "Как в системе": "System",
  "Эмаль": "Enamel",
  "Ночь": "Night",
  "Мята": "Mint",
  "Крафт": "Kraft",
  "Полночь": "Midnight",
  "Контраст": "High contrast",

  "Добавить человека": "Add a person",
  "Имя": "Name",
  "Google-почта": "Google address",
  "Администратор": "Administrator",
  "Цвет магнита": "Magnet colour",
  "Цвет {color}": "Colour {color}",
  "Отменить правку": "Discard changes",
  "Мой профиль": "My profile",
  "Изменить": "Edit",
  "Изменить: {name}": "Edit: {name}",
  "Оставьте пустым, чтобы не менять": "Leave empty to keep it",
  "Удалить {name}? Его задачи останутся без исполнителя.": "Delete {name}? Their tasks will be left without an assignee.",
  "В работе: {n}": "In progress: {n}",
  "сделано за неделю: {n}": "done this week: {n}",
  ", админ": ", admin",
  "Telegram подключён": "Telegram connected",
  "Уведомления в Telegram выключены. Чтобы включить, создайте бота у @BotFather и запустите сервер с переменной TELEGRAM_BOT_TOKEN — подробности в README.":
    "Telegram notifications are off. To turn them on, create a bot with @BotFather and start the server with TELEGRAM_BOT_TOKEN set — see the README.",
  "Уведомления приходят вам в Telegram.": "Notifications come to your Telegram.",
  "Отключить": "Disconnect",
  "Получайте в Telegram новые задачи, комментарии и утреннюю сводку.":
    "Get new tasks, comments and a morning digest in Telegram.",
  "Подключить Telegram": "Connect Telegram",
  "В открывшемся чате нажмите «Start». Когда вернётесь сюда, статус обновится.":
    "Press Start in the chat that opened. The status updates when you come back here.",
  "Открыть бота ещё раз": "Open the bot again",
}/*EN-END*/;

function t(text, params = {}) {
  const template = LANG === "en" ? (EN[text] ?? text) : text;
  return template.replace(/\{(\w+)\}/g, (match, key) => (key in params ? String(params[key]) : match));
}

// Название задачи в кавычках, принятых в языке.
function q(title) {
  return LANG === "ru" ? `«${title}»` : `“${title}”`;
}

const pluralRules = new Intl.PluralRules(LOCALE);
function pointsWord(n) {
  if (LANG === "en") return n === 1 ? "point" : "points";
  return { one: "очко", few: "очка", many: "очков" }[pluralRules.select(n)] || "очка";
}

// Перевод статичной разметки: текстовые узлы и подписи элементов.
function translateStatic(root = document) {
  if (LANG === "ru") return;
  const normalize = (s) => s.replace(/\s+/g, " ").trim();
  const walker = document.createTreeWalker(root.body || root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const key = normalize(node.nodeValue);
    if (key && key in EN) node.nodeValue = node.nodeValue.replace(node.nodeValue.trim(), EN[key]);
  }
  for (const attr of ["placeholder", "aria-label", "title"]) {
    for (const el of (root.body || root).querySelectorAll(`[${attr}]`)) {
      const key = normalize(el.getAttribute(attr));
      if (key in EN) el.setAttribute(attr, EN[key]);
    }
  }
  document.title = t(document.title);
}

translateStatic();
