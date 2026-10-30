"use strict";

// Подпись пустой колонки зависит от роли: названия колонок у каждой доски свои.
const EMPTY_BY_ROLE = {
  todo: "Пока пусто — добавьте задачу",
  in_progress: "Перетащите сюда то, чем заняты",
  review: "Сюда — то, что нужно проверить",
  done: "Здесь появятся сделанные дела",
};
const ROLES = [
  { id: "todo", label: "Начальная" },
  { id: "in_progress", label: "В работе" },
  { id: "review", label: "Проверка" },
  { id: "done", label: "Готово" },
];
// Старые записи истории хранят только роль: тогда колонки ещё не переименовывались.
const LEGACY_COLUMN_TITLE = { todo: "To Do", in_progress: "In Progress", review: "Review", done: "Done" };
const COLORS = ["#2F5BD3", "#E0533D", "#2E9E6A", "#D99A12", "#8A4FD8", "#1F9BB0", "#D9488F", "#4A5A6A"];
const RECURRENCE = { daily: "каждый день", weekly: "каждую неделю", monthly: "каждый месяц" };  // ключи t()
const FIELD_NAMES = {
  title: "название", description: "описание", priority: "важность", category: "категорию",
  due_date: "срок", recurrence: "повтор", points: "очки", parent_id: "родительскую задачу",
};
const LINK_TYPES = {
  blocked_by: { label: "Ждёт", phrase: "ждёт" },
  blocks: { label: "Блокирует", phrase: "блокирует" },
  relates: { label: "Связана с", phrase: "связана с" },
};
const linkPhrase = (type) => (LINK_TYPES[type] ? t(LINK_TYPES[type].phrase) : type);
const THEMES = [
  { id: "auto", name: "Как в системе" },
  { id: "light", name: "Эмаль" },
  { id: "dark", name: "Ночь" },
  { id: "mint", name: "Мята" },
  { id: "kraft", name: "Крафт" },
  { id: "midnight", name: "Полночь" },
  { id: "contrast", name: "Контраст" },
];
const DONE_RECENT_DAYS = 14;
const REFRESH_MS = 30_000;

function storedBoardId() {
  try {
    return Number(localStorage.getItem("board")) || null;
  } catch {
    return null;
  }
}

const state = {
  me: null,
  config: { telegram: false, bot_username: null, site_title: "" },
  boards: [],
  boardId: storedBoardId(),
  settingsBoardId: null,
  users: [],
  tasks: [],
  stats: [],
  filter: { assignee: null, category: "", q: "" },
  showOldDone: false,
  editingTask: null,
  draftChecklist: [],
  editingUser: null,
  dragId: null,
  ratingPeriod: "week",
  offline: false,
};

const $ = (sel) => document.querySelector(sel);

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k === "checked") el.checked = Boolean(v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) el.append(c);
  return el;
}

// ---------- API ----------

class ApiError extends Error {}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      // Язык интерфейса: на нём сервер отвечает ошибками и шлёт уведомления в Telegram.
      headers: body ? { "Content-Type": "application/json", "Accept-Language": LANG } : { "Accept-Language": LANG },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    setOffline(true);
    throw new ApiError(t("Нет связи с сервером. Изменение не сохранено."));
  }
  // Ответ из кеша service worker'а помечен X-Offline: сеть недоступна.
  setOffline(res.headers.get("X-Offline") === "1");
  if (res.status === 401 && path !== "/api/login") {
    // Сессия кончилась посреди работы. При старте экран входа покажет start().
    if (state.me) {
      state.me = null;
      showAuth();
    }
    throw new ApiError(t("Нужно войти"));
  }
  if (!res.ok) {
    let msg = t("Ошибка {status}", { status: res.status });
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = t("Проверьте поля: {fields}", { fields: data.detail.map((d) => d.loc.at(-1)).join(", ") });
    } catch {}
    throw new ApiError(msg);
  }
  return res.status === 204 ? null : res.json();
}

// ---------- даты ----------

const fmtDay = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short" });
const fmtStamp = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function todayISO() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function dueInfo(iso) {
  const days = Math.round((Date.parse(iso) - Date.parse(todayISO())) / 86400000);
  if (days < 0) return { text: days === -1 ? t("вчера") : t("просрочено, {date}", { date: fmtDay.format(new Date(iso)) }), cls: "overdue" };
  if (days === 0) return { text: t("сегодня"), cls: "today" };
  if (days === 1) return { text: t("завтра"), cls: "" };
  return { text: fmtDay.format(new Date(iso)), cls: "" };
}

function utcToLocal(s) {
  return s ? new Date(s.replace(" ", "T") + "Z") : null;
}

// ---------- авторизация ----------

let setupMode = false;

const LOGIN_ERRORS = {
  not_allowed: "Этот Google-аккаунт не допущен к доске. Попросите администратора указать вашу почту в «Люди».",
  state: "Вход через Google прервался. Попробуйте ещё раз.",
  token: "Google не подтвердил вход. Попробуйте ещё раз.",
  cancelled: "Вход через Google отменён.",
};

async function showAuth() {
  clearSavedData();
  $("#app").hidden = true;
  $("#auth").hidden = false;
  let options = { needs_setup: false, google: false, password_login: true };
  try {
    options = await api("GET", "/api/setup");
  } catch {}
  setupMode = options.needs_setup;
  applySiteTitle(options.site_title);
  // Первого администратора создают паролем, даже если потом пароли выключат.
  const withPassword = setupMode || options.password_login;
  const withGoogle = !setupMode && options.google;
  const f = $("#auth-form");
  $("#password-fields").hidden = !withPassword;
  $("#google-login").hidden = !withGoogle;
  $("#auth-or").hidden = !(withGoogle && withPassword);
  $("#auth-name-field").hidden = !setupMode;
  f.display_name.required = setupMode;
  f.password.autocomplete = setupMode ? "new-password" : "current-password";
  $("#auth-hint").textContent = setupMode
    ? t("Создайте первого пользователя — он станет администратором и сможет добавить остальных.")
    : t("Войдите, чтобы увидеть доску.");
  $("#auth-submit").textContent = setupMode ? t("Создать и войти") : t("Войти");

  const loginError = new URLSearchParams(location.search).get("login_error");
  if (loginError) {
    $("#auth-error").textContent = t(LOGIN_ERRORS[loginError] || "Не удалось войти через Google.");
    history.replaceState(null, "", "/");
  }
  if (withPassword) f.username.focus();
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  $("#auth-error").textContent = "";
  try {
    if (setupMode) {
      await api("POST", "/api/setup", {
        username: f.username.value.trim(),
        display_name: f.display_name.value.trim(),
        password: f.password.value,
      });
    } else {
      await api("POST", "/api/login", { username: f.username.value.trim(), password: f.password.value });
    }
    f.reset();
    await start();
  } catch (err) {
    $("#auth-error").textContent = err.message;
  }
});

$("#logout").addEventListener("click", async () => {
  await api("POST", "/api/logout");
  state.me = null;
  showAuth();
});

// ---------- загрузка ----------

async function loadAll() {
  const [me, users, boards, stats] = await Promise.all([
    api("GET", "/api/me"),
    api("GET", "/api/users"),
    api("GET", "/api/boards"),
    api("GET", "/api/stats"),
  ]);
  // Запомненную доску могли удалить.
  if (!boards.some((b) => b.id === state.boardId)) state.boardId = boards[0].id;
  const tasks = await api("GET", `/api/tasks?board_id=${state.boardId}`);
  Object.assign(state, { me, users, boards, tasks, stats });
  syncTheme();
  syncLang();
  renderAll();
}

function renderVersion() {
  const { version } = state.config;
  const link = $("#app-version");
  link.textContent = /^\d/.test(version) ? `v${version}` : version;
  link.hidden = !version;
}

function applySiteTitle(title) {
  const name = title || t("Доска задач");
  document.querySelectorAll(".wordmark").forEach((el) => { el.textContent = name; });
  document.title = name;
  $('meta[name="apple-mobile-web-app-title"]').setAttribute("content", name);
}

function renderBoardSelect() {
  const select = $("#board-select");
  select.replaceChildren(...state.boards.map((b) => h("option", { value: b.id }, boardName(b))));
  select.value = state.boardId;
  select.hidden = state.boards.length < 2;
}

async function switchBoard(id) {
  state.boardId = id;
  try { localStorage.setItem("board", String(id)); } catch {}
  state.filter.category = "";
  state.showOldDone = false;
  await loadAll();
}

$("#board-select").addEventListener("change", (e) => switchBoard(Number(e.target.value)));

function renderAll() {
  renderBoardSelect();
  $("#open-rating").hidden = !pointsOn();
  $("#open-settings").hidden = !state.me?.is_admin;
  renderPeopleFilter();
  renderCategoryFilter();
  renderBoard();
  if ($("#settings-dialog").open) renderSettings();
  if ($("#people-dialog").open) {
    renderPeopleList();
    renderTelegramBox();
  }
}

async function start() {
  try {
    state.me = await api("GET", "/api/me");
  } catch {
    if (state.offline) {
      await showAuth();
      $("#auth-error").textContent = t("Нет связи с сервером. Войти можно будет, когда появится интернет.");
      return;
    }
    return showAuth();
  }
  $("#auth").hidden = true;
  $("#app").hidden = false;
  state.config = await api("GET", "/api/config");
  applySiteTitle(state.config.site_title);
  renderVersion();
  await loadAll();
}

setInterval(() => {
  if (state.me && !document.hidden && !state.dragId && !document.querySelector("dialog[open]")) loadAll().catch(() => {});
}, REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  // Установленное приложение на телефоне может висеть открытым днями — проверяем релиз.
  swRegistration?.update().catch(() => {});
  if (state.me && !$("#task-dialog").open) loadAll().catch(() => {});
});

// ---------- фильтры ----------

const userById = (id) => state.users.find((u) => u.id === id);
const userName = (id) => userById(id)?.display_name ?? t("кто-то");

function magnet(user, extra = "") {
  if (!user) return h("span", { class: `magnet empty ${extra}`, title: t("Никто не назначен") }, "?");
  const initial = user.display_name.trim().charAt(0).toUpperCase();
  return h("span", { class: `magnet ${extra}`, style: `--c:${user.color}`, title: user.display_name }, initial);
}

function renderPeopleFilter() {
  const box = $("#people-filter");
  const pick = (id) => {
    state.filter.assignee = state.filter.assignee === id ? null : id;
    renderPeopleFilter();
    renderBoard();
  };
  box.replaceChildren(
    h("button", { class: "chip all", "aria-pressed": String(state.filter.assignee === null), onclick: () => pick(null) }, t("Все")),
    ...state.users.map((u) =>
      h("button", {
        class: "chip",
        "aria-pressed": String(state.filter.assignee === u.id),
        title: t("Показать задачи: {name}", { name: u.display_name }),
        onclick: () => pick(u.id),
      }, magnet(u), h("span", { class: "chip-name" }, u.display_name)),
    ),
  );
}

function renderCategoryFilter() {
  const cats = [...new Set(state.tasks.map((t) => t.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, LOCALE));
  if (state.filter.category && !cats.includes(state.filter.category)) state.filter.category = "";
  $("#category-filter").replaceChildren(
    h("option", { value: "" }, t("Все категории")),
    ...cats.map((c) => h("option", { value: c, selected: c === state.filter.category }, c)),
  );
  $("#category-filter").hidden = cats.length === 0;
  $("#categories").replaceChildren(...cats.map((c) => h("option", { value: c })));
}

$("#category-filter").addEventListener("change", (e) => {
  state.filter.category = e.target.value;
  renderBoard();
});
$("#search").addEventListener("input", (e) => {
  state.filter.q = e.target.value.trim().toLowerCase();
  renderBoard();
});

function matchesFilter(t) {
  const f = state.filter;
  if (f.assignee !== null && t.assignee_id !== f.assignee) return false;
  if (f.category && t.category !== f.category) return false;
  if (f.q) {
    const text = `${t.title} ${t.description} ${t.checklist.map((i) => i.text).join(" ")}`.toLowerCase();
    if (!text.includes(f.q)) return false;
  }
  return true;
}

// ---------- доска ----------

const taskById = (id) => state.tasks.find((t) => t.id === id);
const boardName = (board) => board.title || t("Моя доска");
const currentBoard = () => state.boards.find((b) => b.id === state.boardId) ?? state.boards[0];
const boardColumns = () => currentBoard()?.columns ?? [];
const columnById = (id) => boardColumns().find((c) => c.id === id);
const columnOrder = (task) => boardColumns().findIndex((c) => c.id === task.column_id);
const pointsOn = () => currentBoard()?.points_enabled !== false;

function childrenOf(id) {
  return state.tasks
    .filter((t) => t.parent_id === id)
    .sort((a, b) => columnOrder(a) - columnOrder(b) || a.position - b.position);
}

// Незакрытые задачи, которых эта ждёт.
function openBlockers(task) {
  return task.links
    .filter((l) => l.type === "blocked_by")
    .map((l) => taskById(l.task_id))
    .filter((b) => b && b.status !== "done");
}

// Правила «предупреждать, а не запрещать»: возвращает текст вопроса или null.
function statusWarning(task, column) {
  if (!task || !column || column.role === task.status) return null;
  const lines = [];
  if (column.role === "done") {
    const open = childrenOf(task.id).filter((c) => c.status !== "done");
    if (open.length) lines.push(t("Не сделаны подзадачи: {list}.", { list: open.map((c) => q(c.title)).join(", ") }));
  }
  if (column.role !== "todo") {
    const blockers = openBlockers(task);
    if (blockers.length) lines.push(t("Задача ждёт: {list}.", { list: blockers.map((b) => q(b.title)).join(", ") }));
  }
  return lines.length
    ? `${lines.join("\n")}\n\n${t("Всё равно перенести {task} в {column}?", { task: q(task.title), column: column.title })}`
    : null;
}

function columnTasks(columnId) {
  return state.tasks
    .filter((t) => t.column_id === columnId)
    .sort((a, b) => a.position - b.position || a.id - b.id);
}

function isOldDone(t) {
  const done = utcToLocal(t.completed_at);
  return done && Date.now() - done.getTime() > DONE_RECENT_DAYS * 86400000;
}

function renderBoard() {
  const board = $("#board");
  board.replaceChildren(
    ...boardColumns().map((col) => {
      let tasks = columnTasks(col.id).filter(matchesFilter);
      let hiddenOld = 0;
      if (col.role === "done" && !state.showOldDone) {
        const recent = tasks.filter((t) => !isOldDone(t));
        hiddenOld = tasks.length - recent.length;
        tasks = recent;
      }
      const list = h("div", { class: "cards", "data-column": col.id },
        tasks.length ? tasks.map(renderCard) : h("p", { class: "empty-col" }, t(EMPTY_BY_ROLE[col.role])));
      wireDropZone(list);
      return h("section", { class: "column", "aria-label": col.title },
        h("div", { class: "column-head" },
          h("h2", {}, col.title),
          h("span", { class: "count" }, String(tasks.length)),
          h("button", { class: "add", title: t("Добавить в {column}", { column: col.title }), "aria-label": t("Добавить в {column}", { column: col.title }), onclick: () => openTask(null, col.id) }, "+"),
        ),
        list,
        hiddenOld ? h("button", { class: "show-old", onclick: () => { state.showOldDone = true; renderBoard(); } },
          t("Показать старые ({n})", { n: hiddenOld })) : null,
      );
    }),
  );
}

function renderCard(task) {
  const meta = [];
  const done = task.status === "done";
  if (task.priority === "high" && !done) meta.push(h("span", { class: "tag urgent" }, t("Срочно")));
  if (task.due_date && !done) {
    const d = dueInfo(task.due_date);
    meta.push(h("span", { class: `tag ${d.cls}` }, d.text));
  }
  if (task.checklist.length) {
    const ready = task.checklist.filter((i) => i.done).length;
    meta.push(h("span", { class: `tag ${ready === task.checklist.length ? "complete" : ""}`, title: t("Чек-лист") },
      `☑ ${ready}/${task.checklist.length}`));
  }
  const children = childrenOf(task.id);
  if (children.length) {
    const ready = children.filter((c) => c.status === "done").length;
    meta.push(h("span", { class: `tag ${ready === children.length ? "complete" : ""}`, title: t("Подзадачи") },
      `▤ ${ready}/${children.length}`));
  }
  const blockers = done ? [] : openBlockers(task);
  if (blockers.length) {
    const more = blockers.length > 1 ? t(" и ещё {n}", { n: blockers.length - 1 }) : "";
    meta.push(h("span", { class: "tag wait", title: blockers.map((b) => b.title).join(", ") },
      t("⏳ ждёт: {title}{more}", { title: blockers[0].title, more })));
  }
  if (task.recurrence !== "none") meta.push(h("span", { class: "tag", title: t("Повторяется") }, `↻ ${t(RECURRENCE[task.recurrence])}`));
  if (task.category) meta.push(h("span", { class: "tag" }, task.category));
  // Очки — последним элементом строки меток: в углу карточки они наезжали на магнит.
  if (task.points > 1 && pointsOn()) {
    meta.push(h("span", { class: "points", title: t("{n} {points} за выполнение", { n: task.points, points: pointsWord(task.points) }) }, `+${task.points}`));
  }

  const card = h("article", {
    class: `card ${task.priority} ${done ? "done" : ""}`,
    draggable: "true",
    tabindex: "0",
    "data-id": task.id,
    onclick: () => openTask(task),
    onkeydown: (e) => { if (e.key === "Enter") openTask(task); },
    ondragstart: (e) => {
      state.dragId = task.id;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(task.id));
      requestAnimationFrame(() => card.classList.add("dragging"));
    },
    ondragend: () => {
      state.dragId = null;
      card.classList.remove("dragging");
      document.querySelectorAll(".drop-marker").forEach((m) => m.remove());
    },
  },
    magnet(userById(task.assignee_id)),
    task.parent_id && taskById(task.parent_id)
      ? h("div", { class: "parent-ref", title: t("Входит в задачу") }, `↳ ${taskById(task.parent_id).title}`) : null,
    h("div", { class: "title" }, task.title),
    h("div", { class: "meta" }, meta),
  );
  return card;
}

// ---------- перетаскивание ----------

function cardAfterPointer(list, y) {
  const cards = [...list.querySelectorAll(".card:not(.dragging)")];
  return cards.find((c) => {
    const r = c.getBoundingClientRect();
    return y < r.top + r.height / 2;
  }) || null;
}

function wireDropZone(list) {
  list.addEventListener("dragover", (e) => {
    if (state.dragId == null) return;
    e.preventDefault();
    const after = cardAfterPointer(list, e.clientY);
    let marker = document.querySelector(".drop-marker");
    if (!marker) marker = h("div", { class: "drop-marker" });
    list.querySelector(".empty-col")?.setAttribute("hidden", "");
    if (after) list.insertBefore(marker, after);
    else list.append(marker);
  });
  list.addEventListener("dragleave", (e) => {
    if (!list.contains(e.relatedTarget)) {
      list.querySelector(".drop-marker")?.remove();
      list.querySelector(".empty-col")?.removeAttribute("hidden");
    }
  });
  list.addEventListener("drop", async (e) => {
    e.preventDefault();
    const id = state.dragId;
    if (id == null) return;
    const column = columnById(Number(list.dataset.column));
    const before = cardAfterPointer(list, e.clientY);
    // Индекс считаем по полной колонке, а не только по видимым после фильтра карточкам.
    const full = columnTasks(column.id).map((t) => t.id).filter((x) => x !== id);
    let index;
    if (before) {
      index = full.indexOf(Number(before.dataset.id));
    } else {
      const visible = [...list.querySelectorAll(".card:not(.dragging)")];
      const last = visible.at(-1);
      index = last ? full.indexOf(Number(last.dataset.id)) + 1 : full.length;
    }
    state.dragId = null;
    const task = state.tasks.find((t) => t.id === id);
    const warning = statusWarning(task, column);
    if (warning && !confirm(warning)) {
      renderBoard();
      return;
    }
    const wasDone = task.status === "done";
    // Оптимистично переставляем локально, потом сверяемся с сервером.
    full.splice(index, 0, id);
    task.column_id = column.id;
    task.status = column.role;
    full.forEach((tid, pos) => { state.tasks.find((t) => t.id === tid).position = pos; });
    renderBoard();
    try {
      await api("POST", `/api/tasks/${id}/move`, { column_id: column.id, index });
      if (column.role === "done" && !wasDone) celebrate(task);
    } catch (err) {
      toast(err.message);
    }
    loadAll();
  });
}

function celebrate(task) {
  const parts = [];
  if (task.assignee_id && task.points && pointsOn()) {
    parts.push(t("{name}: +{n} {points}", { name: userName(task.assignee_id), n: task.points, points: pointsWord(task.points) }));
  }
  if (task.recurrence !== "none") parts.push(t("следующий повтор добавлен в To Do"));
  if (parts.length) toast(parts.join(", "));
}

// ---------- карточка задачи ----------

const taskDialog = $("#task-dialog");
const taskForm = $("#task-form");

function openTask(task, columnId = null, parentId = null) {
  state.editingTask = task;
  $("#task-error").textContent = "";
  $("#task-dialog-title").textContent = task ? t("Задача") : t("Новая задача");
  $("#task-save").textContent = task ? t("Сохранить") : t("Добавить");
  $("#task-delete").hidden = !task;
  $("#activity").hidden = !task;
  taskDialog.classList.toggle("wide", Boolean(task));

  taskForm.assignee_id.replaceChildren(
    h("option", { value: "" }, t("Никто")),
    ...state.users.map((u) => h("option", { value: u.id }, u.display_name)),
  );
  taskForm.column_id.replaceChildren(...boardColumns().map((c) => h("option", { value: c.id }, c.title)));
  $("#points-field").hidden = !pointsOn();

  const values = task || {
    title: "", description: "", column_id: columnId ?? boardColumns().find((c) => c.role === "todo")?.id, priority: "normal", category: state.filter.category,
    assignee_id: state.filter.assignee ?? state.me.id, due_date: null, recurrence: "none", points: 1,
    parent_id: parentId,
  };
  taskForm.title.value = values.title;
  taskForm.description.value = values.description;
  taskForm.assignee_id.value = values.assignee_id ?? "";
  taskForm.column_id.value = values.column_id;
  taskForm.due_date.value = values.due_date ?? "";
  taskForm.recurrence.value = values.recurrence;
  taskForm.category.value = values.category;
  ensurePointsOption(values.points);
  taskForm.points.value = String(values.points);
  taskForm.querySelector(`input[name="priority"][value="${values.priority}"]`).checked = true;

  fillParentSelect(task, values.parent_id ?? null);
  $("#subtask-input").value = "";
  renderRelations();

  state.draftChecklist = [];
  $("#checklist-input").value = "";
  renderChecklist();
  if (task) {
    $("#events").replaceChildren();
    $("#comment-form").reset();
    loadEvents();
  }
  taskDialog.showModal();
  if (!task) taskForm.title.focus();
}

// Очки могли выставить через API нестандартным числом — показываем его, а не теряем.
function ensurePointsOption(points) {
  const select = taskForm.points;
  if (![...select.options].some((o) => o.value === String(points))) {
    select.append(h("option", { value: points }, String(points)));
  }
}

taskDialog.addEventListener("close", () => {
  if (state.editingTask) loadAll().catch(() => {});
});

taskForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = taskForm;
  const body = {
    title: f.title.value.trim(),
    description: f.description.value,
    column_id: Number(f.column_id.value),
    priority: f.querySelector('input[name="priority"]:checked').value,
    category: f.category.value.trim(),
    assignee_id: f.assignee_id.value ? Number(f.assignee_id.value) : null,
    due_date: f.due_date.value || null,
    recurrence: f.recurrence.value,
    points: Number(f.points.value),
  };
  // Поле недоступно, если у задачи свои подзадачи: тогда родителя не трогаем.
  if (!f.parent_id.disabled) body.parent_id = f.parent_id.value ? Number(f.parent_id.value) : null;
  const task = state.editingTask;
  const column = columnById(body.column_id);
  const warning = statusWarning(task, column);
  if (warning && !confirm(warning)) return;
  try {
    if (task) {
      await api("PATCH", `/api/tasks/${task.id}`, body);
      if (task.status !== "done" && column.role === "done") celebrate({ ...task, ...body });
    } else {
      await api("POST", "/api/tasks", { ...body, board_id: state.boardId, checklist: state.draftChecklist.map((i) => i.text) });
    }
    taskDialog.close();
    await loadAll();
  } catch (err) {
    $("#task-error").textContent = err.message;
  }
});

$("#task-delete").addEventListener("click", async () => {
  const task = state.editingTask;
  if (!task) return;
  const warn = task.status === "done" && task.points ? t(" Очки за неё пропадут из рейтинга.") : "";
  if (!confirm(t("Удалить задачу {task}?{warn}", { task: q(task.title), warn }))) return;
  try {
    await api("DELETE", `/api/tasks/${task.id}`);
    state.editingTask = null;
    taskDialog.close();
    await loadAll();
  } catch (err) {
    $("#task-error").textContent = err.message;
  }
});

$("#new-task").addEventListener("click", () => openTask(null));

// ---------- подзадачи и связи ----------

function fillParentSelect(task, parentId) {
  const select = taskForm.parent_id;
  const hasChildren = Boolean(task) && childrenOf(task.id).length > 0;
  // Родителем может быть только самостоятельная задача: подзадачи — один уровень.
  const candidates = state.tasks
    .filter((t) => t.parent_id == null && t.id !== task?.id && (t.status !== "done" || t.id === parentId))
    .sort((a, b) => a.title.localeCompare(b.title, LOCALE));
  select.replaceChildren(
    h("option", { value: "" }, t("Ни в какую, самостоятельная задача")),
    ...candidates.map((t) => h("option", { value: t.id }, t.title)),
  );
  select.value = parentId ?? "";
  select.disabled = hasChildren;
  select.title = hasChildren ? t("У задачи есть свои подзадачи, поэтому сама она подзадачей быть не может") : "";
}

// Открыть другую задачу из окна текущей.
function switchTask(task) {
  taskDialog.close();
  openTask(task);
}

function relRow(task, { before = null, after = null } = {}) {
  return h("li", { class: task.status === "done" ? "done" : "" },
    before,
    h("button", { type: "button", class: "rel-title", onclick: () => switchTask(task) }, task.title),
    h("span", { class: `status-pill s-${task.status}` }, columnById(task.column_id)?.title ?? ""),
    magnet(userById(task.assignee_id), "sm"),
    after,
  );
}

function renderRelations() {
  const task = state.editingTask;
  const canHaveChildren = Boolean(task) && task.parent_id == null;
  $("#subtasks-section").hidden = !canHaveChildren;
  if (canHaveChildren) {
    const children = childrenOf(task.id);
    const ready = children.filter((c) => c.status === "done").length;
    $("#subtasks-progress").textContent = children.length ? t("{ready} из {total}", { ready, total: children.length }) : "";
    $("#subtasks-list").replaceChildren(...children.map((c) => relRow(c)));
  }

  $("#links-section").hidden = !task;
  if (!task) return;
  const order = ["blocked_by", "blocks", "relates"];
  const links = [...task.links].sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type));
  $("#links-list").replaceChildren(
    ...links.map((l) => {
      const other = taskById(l.task_id);
      if (!other) return null;
      return relRow(other, {
        before: h("span", { class: `link-type ${l.type}` }, t(LINK_TYPES[l.type].label)),
        after: h("button", {
          type: "button", class: "icon-btn", "aria-label": t("Убрать связь с {task}", { task: q(other.title) }),
          onclick: () => removeLink(l.id),
        }, "×"),
      });
    }),
  );
  const linked = new Set(task.links.map((l) => l.task_id));
  const targets = state.tasks
    .filter((t) => t.id !== task.id && !linked.has(t.id) && !isOldDone(t))
    .sort((a, b) => columnOrder(a) - columnOrder(b) || a.title.localeCompare(b.title, LOCALE));
  $("#link-target").replaceChildren(
    h("option", { value: "" }, t("Выберите задачу")),
    ...boardColumns().map((c) => {
      const group = targets.filter((t) => t.column_id === c.id);
      return group.length ? h("optgroup", { label: c.title }, group.map((t) => h("option", { value: t.id }, t.title))) : null;
    }),
  );
}

// Перечитать доску и обновить открытое окно, не трогая несохранённые поля формы.
async function refreshEditing() {
  const id = state.editingTask?.id;
  await loadAll();
  const fresh = taskById(id);
  if (!fresh || !taskDialog.open) return;
  state.editingTask = fresh;
  renderRelations();
  renderChecklist();
  loadEvents();
}

async function addSubtask() {
  const input = $("#subtask-input");
  const title = input.value.trim();
  const parent = state.editingTask;
  if (!title || !parent) return;
  try {
    await api("POST", "/api/tasks", {
      title, parent_id: parent.id, assignee_id: parent.assignee_id, category: parent.category,
    });
    input.value = "";
    await refreshEditing();
    input.focus();
  } catch (err) {
    $("#task-error").textContent = err.message;
  }
}

async function addLink() {
  const task = state.editingTask;
  const target = $("#link-target").value;
  if (!task || !target) return;
  try {
    await api("POST", `/api/tasks/${task.id}/links`, { kind: $("#link-kind").value, task_id: Number(target) });
    $("#task-error").textContent = "";
    await refreshEditing();
  } catch (err) {
    $("#task-error").textContent = err.message;
  }
}

async function removeLink(linkId) {
  try {
    await api("DELETE", `/api/links/${linkId}`);
    await refreshEditing();
  } catch (err) {
    $("#task-error").textContent = err.message;
  }
}

$("#subtask-add").addEventListener("click", addSubtask);
$("#subtask-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addSubtask();
  }
});
$("#link-add").addEventListener("click", addLink);

// ---------- чек-лист ----------

// У новой задачи пункты копятся локально и уходят вместе с ней; у существующей сохраняются сразу.
function checklistItems() {
  return state.editingTask ? state.editingTask.checklist : state.draftChecklist;
}

function renderChecklist() {
  const items = checklistItems();
  const ready = items.filter((i) => i.done).length;
  $("#checklist-progress").textContent = items.length ? t("{ready} из {total}", { ready, total: items.length }) : "";
  $("#checklist-items").replaceChildren(
    ...items.map((item) =>
      h("li", { class: item.done ? "done" : "" },
        h("label", {},
          h("input", { type: "checkbox", checked: item.done, onchange: (e) => toggleItem(item, e.target.checked) }),
          h("span", {}, item.text),
        ),
        h("button", { type: "button", class: "icon-btn", "aria-label": t("Удалить пункт {item}", { item: q(item.text) }), onclick: () => removeItem(item) }, "×"),
      ),
    ),
  );
}

async function addItem() {
  const input = $("#checklist-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  const task = state.editingTask;
  if (!task) {
    state.draftChecklist.push({ id: `draft-${Date.now()}`, text, done: false });
  } else {
    try {
      task.checklist.push(await api("POST", `/api/tasks/${task.id}/checklist`, { text }));
    } catch (err) {
      $("#task-error").textContent = err.message;
    }
  }
  renderChecklist();
  input.focus();
}

async function toggleItem(item, done) {
  item.done = done;
  renderChecklist();
  if (state.editingTask) {
    try {
      await api("PATCH", `/api/checklist/${item.id}`, { done });
    } catch (err) {
      item.done = !done;
      renderChecklist();
      $("#task-error").textContent = err.message;
    }
  }
}

async function removeItem(item) {
  const list = checklistItems();
  list.splice(list.indexOf(item), 1);
  renderChecklist();
  if (state.editingTask) {
    try {
      await api("DELETE", `/api/checklist/${item.id}`);
    } catch (err) {
      $("#task-error").textContent = err.message;
    }
  }
}

$("#checklist-add").addEventListener("click", addItem);
$("#checklist-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addItem();
  }
});

// ---------- история и комментарии ----------

async function loadEvents() {
  const task = state.editingTask;
  const events = await api("GET", `/api/tasks/${task.id}/events`);
  if (state.editingTask !== task) return;
  $("#events").replaceChildren(...events.map(renderEvent));
  const box = $("#events");
  box.scrollTop = box.scrollHeight;
}

function describeEvent(e) {
  const p = e.payload;
  switch (e.kind) {
    case "created":
      return p.recurring_from ? t("создаёт следующий повтор") : t("создаёт задачу");
    case "status":
      return t("переносит из {from} в {to}", {
        from: p.from_title ?? LEGACY_COLUMN_TITLE[p.from], to: p.to_title ?? LEGACY_COLUMN_TITLE[p.to],
      });
    case "assigned":
      return p.to ? t("назначает исполнителем: {name}", { name: userName(p.to) }) : t("снимает исполнителя");
    case "edited":
      return t("меняет {fields}", { fields: p.fields.map((f) => (FIELD_NAMES[f] ? t(FIELD_NAMES[f]) : f)).join(", ") });
    case "linked":
      return t("связывает: {phrase} {task}", { phrase: linkPhrase(p.type), task: q(taskById(p.other)?.title ?? t("удалённая задача")) });
    case "unlinked":
      return t("убирает связь: {phrase} {task}", { phrase: linkPhrase(p.type), task: q(taskById(p.other)?.title ?? t("удалённая задача")) });
    default:
      return e.kind;
  }
}

function renderEvent(e) {
  const author = userById(e.user_id);
  const when = h("time", { datetime: e.created_at }, fmtStamp.format(utcToLocal(e.created_at)));
  const name = h("b", {}, author ? author.display_name : t("Удалённый пользователь"));
  if (e.kind !== "comment") {
    return h("li", { class: "event" }, name, " ", describeEvent(e), h("span", { class: "muted" }, ", "), when);
  }
  const canDelete = e.user_id === state.me.id || state.me.is_admin;
  return h("li", { class: "comment" },
    magnet(author),
    h("div", { class: "bubble" },
      h("div", { class: "bubble-head" }, name, when,
        canDelete ? h("button", { type: "button", class: "link-btn", onclick: () => deleteComment(e) }, t("удалить")) : null),
      h("p", {}, e.payload.text),
    ),
  );
}

async function deleteComment(e) {
  if (!confirm(t("Удалить комментарий?"))) return;
  try {
    await api("DELETE", `/api/events/${e.id}`);
    loadEvents();
  } catch (err) {
    toast(err.message);
  }
}

$("#comment-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const field = e.target.text;
  const text = field.value.trim();
  if (!text || !state.editingTask) return;
  try {
    await api("POST", `/api/tasks/${state.editingTask.id}/comments`, { text });
    field.value = "";
    loadEvents();
  } catch (err) {
    toast(err.message);
  }
});
$("#comment-form").text.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) $("#comment-form").requestSubmit();
});

// ---------- рейтинг ----------

const ratingDialog = $("#rating-dialog");

async function renderRating() {
  document.querySelectorAll("#rating-periods button").forEach((b) => {
    b.setAttribute("aria-selected", String(b.dataset.period === state.ratingPeriod));
  });
  const rows = await api("GET", `/api/leaderboard?period=${state.ratingPeriod}&board_id=${state.boardId}`);
  const max = Math.max(1, ...rows.map((r) => r.points));
  $("#rating-list").replaceChildren(
    ...rows.map((r, i) => {
      const u = userById(r.user_id);
      const leader = i === 0 && r.points > 0;
      return h("li", { class: leader ? "leader" : "" },
        h("span", { class: "place" }, leader ? "🏆" : String(i + 1)),
        magnet(u, leader ? "lg" : ""),
        h("div", { class: "who" },
          h("div", { class: "row" },
            h("b", {}, u ? u.display_name : "—"),
            h("span", { class: "score" }, `${r.points} ${pointsWord(r.points)}`),
          ),
          h("div", { class: "bar" }, h("span", { style: `width:${(r.points / max) * 100}%;--c:${u?.color}` })),
          h("div", { class: "muted small" }, t("Выполнено задач: {n}", { n: r.done })),
        ),
      );
    }),
  );
}

$("#open-rating").addEventListener("click", () => {
  ratingDialog.showModal();
  renderRating().catch((err) => toast(err.message));
});
$("#rating-periods").addEventListener("click", (e) => {
  const period = e.target.closest("button")?.dataset.period;
  if (!period) return;
  state.ratingPeriod = period;
  renderRating().catch((err) => toast(err.message));
});

// ---------- темы ----------

const themeDialog = $("#theme-dialog");
let themeSaving = false;

function currentTheme() {
  return document.documentElement.dataset.theme || "auto";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch {}
  updateThemeColor();
}

// Цвет панели браузера на телефоне — под фон текущей темы.
function updateThemeColor() {
  const color = getComputedStyle(document.documentElement).getPropertyValue("--enamel").trim();
  if (color) $('meta[name="theme-color"]').setAttribute("content", color);
}

// Тема из профиля важнее локальной: её могли сменить на другом устройстве.
function syncTheme() {
  if (!themeSaving && state.me?.theme && state.me.theme !== currentTheme()) applyTheme(state.me.theme);
}

function renderThemes() {
  const active = currentTheme();
  $("#theme-list").replaceChildren(
    ...THEMES.map((theme) =>
      h("button", {
        type: "button", class: "theme-tile", role: "radio", "aria-checked": String(theme.id === active),
        onclick: () => chooseTheme(theme.id),
      },
        h("span", { class: "theme-preview", "data-theme": theme.id, "aria-hidden": "true" },
          h("span", { class: "pv-col" }, h("span", { class: "pv-card" }), h("span", { class: "pv-card urgent short" })),
          h("span", { class: "pv-col" }, h("span", { class: "pv-card accent" })),
          h("span", { class: "pv-col" }, h("span", { class: "pv-card short" })),
        ),
        h("span", { class: "theme-name" }, t(theme.name)),
      ),
    ),
  );
}

async function chooseTheme(theme) {
  const previous = currentTheme();
  applyTheme(theme);
  renderThemes();
  themeSaving = true;
  try {
    state.me = await api("PATCH", `/api/users/${state.me.id}`, { theme });
  } catch (err) {
    applyTheme(previous);
    renderThemes();
    toast(t("Тема не сохранилась: {error}", { error: err.message }));
  } finally {
    themeSaving = false;
  }
}

$("#open-theme").addEventListener("click", () => {
  renderThemes();
  renderLangChoice();
  themeDialog.showModal();
});

// ---------- язык ----------

let langSaving = false;

function storeLangChoice(choice) {
  try {
    localStorage.setItem("lang", choice);
    return localStorage.getItem("lang") === choice;
  } catch {
    return false;
  }
}

function renderLangChoice() {
  const choice = storedLangChoice();
  document.querySelectorAll("#lang-choice button").forEach((b) => {
    b.setAttribute("aria-checked", String(b.dataset.lang === choice));
  });
  document.querySelectorAll(".lang-switch button").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.lang === LANG));
  });
}

// Язык меняется перезагрузкой: так гарантированно перерисуется всё, включая окна.
function applyLangChoice(choice) {
  const stored = storeLangChoice(choice);
  if (stored && effectiveLang(choice) !== LANG) location.reload();
  else renderLangChoice();
}

async function chooseLang(choice) {
  if (state.me) {
    langSaving = true;
    try {
      state.me = await api("PATCH", `/api/users/${state.me.id}`, { lang: choice });
    } catch (err) {
      toast(t("Тема не сохранилась: {error}", { error: err.message }));
      return;
    } finally {
      langSaving = false;
    }
  }
  applyLangChoice(choice);
}

// Выбор из профиля важнее локального: его могли сменить на другом устройстве.
function syncLang() {
  if (!langSaving && state.me?.lang && state.me.lang !== storedLangChoice()) applyLangChoice(state.me.lang);
}

document.querySelectorAll("#lang-choice button, .lang-switch button").forEach((b) => {
  b.addEventListener("click", () => chooseLang(b.dataset.lang));
});
renderLangChoice();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateThemeColor);
updateThemeColor();

// ---------- настройки (только администратор) ----------

const settingsDialog = $("#settings-dialog");
const settingsBoard = () => state.boards.find((b) => b.id === state.settingsBoardId) ?? currentBoard();
let settingsTasks = [];

// Выполняет изменение и перечитывает доски; ошибку показывает в окне.
async function settingsAction(action) {
  $("#settings-error").textContent = "";
  try {
    await action();
    await loadAll();
    await loadSettingsTasks();
    renderSettings();
  } catch (err) {
    $("#settings-error").textContent = err.message;
    renderSettings();
  }
}

async function loadSettingsTasks() {
  settingsTasks = await api("GET", `/api/tasks?board_id=${settingsBoard().id}`);
}

function moveColumn(board, index, delta) {
  const ids = board.columns.map((c) => c.id);
  [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
  settingsAction(() => api("PUT", `/api/boards/${board.id}/columns/order`, { ids }));
}

function deleteColumn(column, moveTo) {
  const query = moveTo ? `?move_to=${moveTo}` : "";
  settingsAction(() => api("DELETE", `/api/columns/${column.id}${query}`));
}

function askDeleteColumn(column, row) {
  const count = settingsTasks.filter((task) => task.column_id === column.id).length;
  if (!count) {
    if (confirm(t("Удалить колонку {column}?", { column: q(column.title) }))) deleteColumn(column);
    return;
  }
  // Задачи вместе с колонкой не удаляются: спрашиваем, куда их перенести.
  const finished = column.role === "done";
  const targets = settingsBoard().columns.filter((c) => c.id !== column.id && (c.role === "done") === finished);
  if (!targets.length) {
    $("#settings-error").textContent = t("Сначала перенесите задачи из этой колонки");
    return;
  }
  const select = h("select", { "aria-label": t("Куда перенести задачи") }, targets.map((c) => h("option", { value: c.id }, c.title)));
  row.querySelector(".move-tasks")?.remove();
  row.append(h("div", { class: "move-tasks" },
    t("Задач в колонке: {n}. Перенести в:", { n: count }), select,
    h("button", { type: "button", class: "btn danger-ghost", onclick: () => deleteColumn(column, Number(select.value)) }, t("Удалить")),
  ));
}

function renderColumnRow(board, column, index) {
  const row = h("li", {},
    h("input", {
      value: column.title, maxlength: 40, "aria-label": t("Название колонки"),
      onchange: (e) => {
        const title = e.target.value.trim();
        if (title) settingsAction(() => api("PATCH", `/api/columns/${column.id}`, { title }));
      },
    }),
    h("select", {
      "aria-label": t("Роль колонки"),
      onchange: (e) => settingsAction(() => api("PATCH", `/api/columns/${column.id}`, { role: e.target.value })),
    }, ROLES.map((r) => h("option", { value: r.id, selected: r.id === column.role }, t(r.label)))),
    h("button", { type: "button", class: "icon-btn", title: t("Выше"), "aria-label": t("Выше"), disabled: index === 0, onclick: () => moveColumn(board, index, -1) }, "↑"),
    h("button", { type: "button", class: "icon-btn", title: t("Ниже"), "aria-label": t("Ниже"), disabled: index === board.columns.length - 1, onclick: () => moveColumn(board, index, 1) }, "↓"),
    h("button", { type: "button", class: "icon-btn", title: t("Удалить колонку"), "aria-label": t("Удалить колонку"), onclick: () => askDeleteColumn(column, row) }, "×"),
  );
  return row;
}

function renderSettings() {
  const board = settingsBoard();
  $("#site-form").site_title.value = state.config.site_title ?? "";
  $("#boards-list").replaceChildren(...state.boards.map((b) => h("li", { class: b.id === board.id ? "current" : "" },
    h("button", {
      type: "button", class: "rel-title",
      onclick: async () => {
        state.settingsBoardId = b.id;
        await loadSettingsTasks();
        renderSettings();
      },
    }, boardName(b)),
  )));
  $("#board-editor-title").textContent = boardName(board);
  $("#board-form").title.value = board.title;
  $("#board-points").checked = board.points_enabled;
  $("#board-delete").disabled = state.boards.length < 2;
  $("#columns-list").replaceChildren(...board.columns.map((c, i) => renderColumnRow(board, c, i)));
  $("#column-add").role.replaceChildren(...ROLES.map((r) => h("option", { value: r.id, selected: r.id === "in_progress" }, t(r.label))));
}

$("#open-settings").addEventListener("click", async () => {
  state.settingsBoardId = state.boardId;
  $("#settings-error").textContent = "";
  await loadSettingsTasks();
  renderSettings();
  settingsDialog.showModal();
});

$("#site-form").addEventListener("submit", (e) => {
  e.preventDefault();
  settingsAction(async () => {
    const { site_title } = await api("PUT", "/api/settings", { site_title: e.target.site_title.value });
    state.config.site_title = site_title;
    applySiteTitle(site_title);
  });
});

$("#board-add").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  settingsAction(async () => {
    const board = await api("POST", "/api/boards", { title: form.title.value.trim() });
    form.reset();
    state.settingsBoardId = board.id;
  });
});

$("#board-form").addEventListener("submit", (e) => {
  e.preventDefault();
  settingsAction(() => api("PATCH", `/api/boards/${settingsBoard().id}`, { title: e.target.title.value.trim() }));
});

$("#board-points").addEventListener("change", (e) => {
  settingsAction(() => api("PATCH", `/api/boards/${settingsBoard().id}`, { points_enabled: e.target.checked }));
});

$("#column-add").addEventListener("submit", (e) => {
  e.preventDefault();
  const form = e.target;
  settingsAction(async () => {
    await api("POST", `/api/boards/${settingsBoard().id}/columns`, { title: form.title.value.trim(), role: form.role.value });
    form.reset();
  });
});

$("#board-delete").addEventListener("click", () => {
  const board = settingsBoard();
  const count = settingsTasks.length;
  const message = count
    ? t("Удалить доску {board} вместе с задачами ({n})? Это нельзя отменить.", { board: q(boardName(board)), n: count })
    : t("Удалить доску {board}?", { board: q(boardName(board)) });
  if (!confirm(message)) return;
  settingsAction(async () => {
    await api("DELETE", `/api/boards/${board.id}${count ? "?with_tasks=true" : ""}`);
    state.settingsBoardId = null;
  });
});

// ---------- люди ----------

const peopleDialog = $("#people-dialog");
const userForm = $("#user-form");
let pickedColor = COLORS[0];

function renderSwatches() {
  $("#swatches").replaceChildren(
    ...COLORS.map((c) =>
      h("button", {
        type: "button", role: "radio", "aria-checked": String(c === pickedColor), "aria-label": t("Цвет {color}", { color: c }),
        onclick: () => { pickedColor = c; renderSwatches(); },
      }, h("span", { class: "magnet", style: `--c:${c}` })),
    ),
  );
}

function renderTelegramBox() {
  const box = $("#telegram-box");
  if (!state.config.telegram) {
    box.hidden = !state.me.is_admin;
    box.replaceChildren(
      h("p", { class: "muted small" },
        t("Уведомления в Telegram выключены. Чтобы включить, создайте бота у @BotFather и запустите сервер с переменной TELEGRAM_BOT_TOKEN — подробности в README.")),
    );
    return;
  }
  box.hidden = false;
  if (state.me.telegram_linked) {
    box.replaceChildren(
      h("p", {}, t("Уведомления приходят вам в Telegram.")),
      h("button", { type: "button", class: "btn ghost", onclick: unlinkTelegram }, t("Отключить")),
    );
  } else {
    box.replaceChildren(
      h("p", {}, t("Получайте в Telegram новые задачи, комментарии и утреннюю сводку.")),
      h("button", { type: "button", class: "btn primary", onclick: linkTelegram }, t("Подключить Telegram")),
    );
  }
}

async function linkTelegram() {
  try {
    const { url } = await api("POST", "/api/me/telegram");
    window.open(url, "_blank", "noopener");
    $("#telegram-box").replaceChildren(
      h("p", {}, t("В открывшемся чате нажмите «Start». Когда вернётесь сюда, статус обновится.")),
      h("a", { href: url, target: "_blank", rel: "noopener", class: "btn ghost" }, t("Открыть бота ещё раз")),
    );
  } catch (err) {
    toast(err.message);
  }
}

async function unlinkTelegram() {
  await api("DELETE", "/api/me/telegram");
  await loadAll();
}

function renderPeopleList() {
  const isAdmin = state.me.is_admin;
  $("#people-list").replaceChildren(
    ...state.users.map((u) => {
      const s = state.stats.find((x) => x.user_id === u.id) || { open: 0, done_week: 0 };
      const canEdit = isAdmin || u.id === state.me.id;
      const meta = [t("В работе: {n}", { n: s.open }), t("сделано за неделю: {n}", { n: s.done_week })];
      if (state.config.telegram && u.telegram_linked) meta.push(t("Telegram подключён"));
      if (isAdmin && u.google_email) meta.push(`Google: ${u.google_email}`);
      return h("li", {},
        magnet(u, "lg"),
        h("div", { class: "who" },
          h("b", {}, u.display_name), " ",
          h("span", { class: "muted small" }, `@${u.username}${u.is_admin ? t(", админ") : ""}`),
          h("div", { class: "muted small" }, meta.join(", ")),
        ),
        h("div", { class: "row-actions" },
          canEdit ? h("button", { class: "btn ghost", onclick: () => editUser(u) }, t("Изменить")) : null,
          isAdmin && u.id !== state.me.id
            ? h("button", { class: "btn danger-ghost", onclick: () => deleteUser(u) }, t("Удалить")) : null,
        ),
      );
    }),
  );
}

function resetUserForm() {
  state.editingUser = null;
  userForm.reset();
  pickedColor = COLORS[state.users.length % COLORS.length];
  const isAdmin = state.me.is_admin;
  userForm.hidden = !isAdmin;
  $("#user-form-title").textContent = t("Добавить человека");
  $("#user-save").textContent = t("Добавить");
  $("#user-cancel").hidden = true;
  $("#user-username-field").hidden = false;
  $("#user-admin-field").hidden = !isAdmin;
  $("#user-google-field").hidden = !isAdmin;
  userForm.username.required = true;
  userForm.password.required = true;
  userForm.password.placeholder = "";
  $("#user-error").textContent = "";
  renderSwatches();
}

function editUser(u) {
  state.editingUser = u;
  userForm.hidden = false;
  $("#user-form-title").textContent = u.id === state.me.id ? t("Мой профиль") : t("Изменить: {name}", { name: u.display_name });
  $("#user-save").textContent = t("Сохранить");
  $("#user-cancel").hidden = false;
  $("#user-username-field").hidden = true;
  $("#user-admin-field").hidden = !state.me.is_admin || u.id === state.me.id;
  // Google-почту назначает только администратор, в том числе себе.
  $("#user-google-field").hidden = !state.me.is_admin;
  userForm.username.required = false;
  userForm.password.required = false;
  userForm.password.value = "";
  userForm.password.placeholder = t("Оставьте пустым, чтобы не менять");
  userForm.display_name.value = u.display_name;
  userForm.is_admin.checked = u.is_admin;
  userForm.google_email.value = u.google_email ?? "";
  pickedColor = u.color;
  $("#user-error").textContent = "";
  renderSwatches();
  userForm.display_name.focus();
}

async function deleteUser(u) {
  if (!confirm(t("Удалить {name}? Его задачи останутся без исполнителя.", { name: u.display_name }))) return;
  try {
    await api("DELETE", `/api/users/${u.id}`);
    if (state.filter.assignee === u.id) state.filter.assignee = null;
    await loadAll();
  } catch (err) {
    $("#user-error").textContent = err.message;
  }
}

userForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = userForm;
  const u = state.editingUser;
  try {
    if (u) {
      const body = { display_name: f.display_name.value.trim(), color: pickedColor };
      if (f.password.value) body.password = f.password.value;
      if (state.me.is_admin && u.id !== state.me.id) body.is_admin = f.is_admin.checked;
      if (state.me.is_admin) body.google_email = f.google_email.value.trim();
      await api("PATCH", `/api/users/${u.id}`, body);
    } else {
      await api("POST", "/api/users", {
        username: f.username.value.trim(),
        display_name: f.display_name.value.trim(),
        password: f.password.value,
        color: pickedColor,
        is_admin: f.is_admin.checked,
        google_email: f.google_email.value.trim() || null,
      });
    }
    await loadAll();
    resetUserForm();
  } catch (err) {
    $("#user-error").textContent = err.message;
  }
});

$("#user-cancel").addEventListener("click", resetUserForm);

$("#open-people").addEventListener("click", () => {
  renderPeopleList();
  renderTelegramBox();
  resetUserForm();
  peopleDialog.showModal();
});

// ---------- общее для диалогов ----------

document.querySelectorAll("dialog").forEach((d) => {
  d.addEventListener("click", (e) => {
    if (e.target === d || e.target.closest("[data-close]")) d.close();
  });
  d.addEventListener("close", reloadIfPending);
});

// ---------- приложение на телефоне (PWA) ----------

let swRegistration = null;
let reloadPending = false;
let installPrompt = null;

function setOffline(offline) {
  if (state.offline === offline) return;
  state.offline = offline;
  $("#offline-banner").hidden = !offline;
  document.body.classList.toggle("offline", offline);
}

function clearSavedData() {
  // Данные доски не должны оставаться на устройстве после выхода.
  if ("caches" in window) caches.delete("data").catch(() => {});
}

// Новая версия перезагружает страницу, но не посреди правки задачи.
function reloadWhenIdle() {
  if (document.querySelector("dialog[open]") || state.dragId != null) reloadPending = true;
  else location.reload();
}

function reloadIfPending() {
  if (reloadPending && !document.querySelector("dialog[open]")) location.reload();
}

if ("serviceWorker" in navigator) {
  let hadController = Boolean(navigator.serviceWorker.controller);
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // Самая первая установка ничего не меняет на экране — перезагружать незачем.
    if (!hadController) {
      hadController = true;
      return;
    }
    reloadWhenIdle();
  });
  navigator.serviceWorker.register("/sw.js")
    .then((registration) => { swRegistration = registration; })
    .catch(() => {});
}

window.addEventListener("online", () => {
  if (state.me) loadAll().catch(() => {});
});
window.addEventListener("offline", () => setOffline(true));

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
  $("#install").hidden = false;
});
window.addEventListener("appinstalled", () => {
  installPrompt = null;
  $("#install").hidden = true;
});
$("#install").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  $("#install").hidden = true;
});

function toast(text) {
  const el = h("div", { class: "toast", role: "status" }, text);
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
}

start();
