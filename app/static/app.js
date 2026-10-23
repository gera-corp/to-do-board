"use strict";

const COLUMNS = [
  { id: "todo", title: "To Do", empty: "Пока пусто — добавьте задачу" },
  { id: "in_progress", title: "In Progress", empty: "Перетащите сюда то, чем заняты" },
  { id: "review", title: "Review", empty: "Сюда — то, что нужно проверить" },
  { id: "done", title: "Done", empty: "Здесь появятся сделанные дела" },
];
const COLUMN_TITLE = Object.fromEntries(COLUMNS.map((c) => [c.id, c.title]));
const COLORS = ["#2F5BD3", "#E0533D", "#2E9E6A", "#D99A12", "#8A4FD8", "#1F9BB0", "#D9488F", "#4A5A6A"];
const RECURRENCE = { daily: "каждый день", weekly: "каждую неделю", monthly: "каждый месяц" };
const FIELD_NAMES = {
  title: "название", description: "описание", priority: "важность", category: "категорию",
  due_date: "срок", recurrence: "повтор", points: "очки", parent_id: "родительскую задачу",
};
const LINK_TYPES = {
  blocked_by: { label: "Ждёт", phrase: "ждёт" },
  blocks: { label: "Блокирует", phrase: "блокирует" },
  relates: { label: "Связана с", phrase: "связана с" },
};
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

const state = {
  me: null,
  config: { telegram: false, bot_username: null },
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

function plural(n, one, few, many) {
  const m = Math.abs(n) % 100;
  if (m >= 11 && m <= 14) return many;
  return { 1: one, 2: few, 3: few, 4: few }[m % 10] || many;
}

// ---------- API ----------

class ApiError extends Error {}

async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    setOffline(true);
    throw new ApiError("Нет связи с сервером. Изменение не сохранено.");
  }
  // Ответ из кеша service worker'а помечен X-Offline: сеть недоступна.
  setOffline(res.headers.get("X-Offline") === "1");
  if (res.status === 401 && path !== "/api/login") {
    // Сессия кончилась посреди работы. При старте экран входа покажет start().
    if (state.me) {
      state.me = null;
      showAuth();
    }
    throw new ApiError("Нужно войти");
  }
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = "Проверьте поля: " + data.detail.map((d) => d.loc.at(-1)).join(", ");
    } catch {}
    throw new ApiError(msg);
  }
  return res.status === 204 ? null : res.json();
}

// ---------- даты ----------

const fmtDay = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short" });
const fmtStamp = new Intl.DateTimeFormat("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function todayISO() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function dueInfo(iso) {
  const days = Math.round((Date.parse(iso) - Date.parse(todayISO())) / 86400000);
  if (days < 0) return { text: days === -1 ? "вчера" : `просрочено, ${fmtDay.format(new Date(iso))}`, cls: "overdue" };
  if (days === 0) return { text: "сегодня", cls: "today" };
  if (days === 1) return { text: "завтра", cls: "" };
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
    ? "Создайте первого пользователя — он станет администратором и сможет добавить остальных."
    : "Войдите, чтобы увидеть доску.";
  $("#auth-submit").textContent = setupMode ? "Создать и войти" : "Войти";

  const loginError = new URLSearchParams(location.search).get("login_error");
  if (loginError) {
    $("#auth-error").textContent = LOGIN_ERRORS[loginError] || "Не удалось войти через Google.";
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
  const [me, users, tasks, stats] = await Promise.all([
    api("GET", "/api/me"),
    api("GET", "/api/users"),
    api("GET", "/api/tasks"),
    api("GET", "/api/stats"),
  ]);
  Object.assign(state, { me, users, tasks, stats });
  syncTheme();
  renderAll();
}

function renderAll() {
  renderPeopleFilter();
  renderCategoryFilter();
  renderBoard();
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
      $("#auth-error").textContent = "Нет связи с сервером. Войти можно будет, когда появится интернет.";
      return;
    }
    return showAuth();
  }
  $("#auth").hidden = true;
  $("#app").hidden = false;
  state.config = await api("GET", "/api/config");
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
const userName = (id) => userById(id)?.display_name ?? "кто-то";

function magnet(user, extra = "") {
  if (!user) return h("span", { class: `magnet empty ${extra}`, title: "Никто не назначен" }, "?");
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
    h("button", { class: "chip all", "aria-pressed": String(state.filter.assignee === null), onclick: () => pick(null) }, "Все"),
    ...state.users.map((u) =>
      h("button", {
        class: "chip",
        "aria-pressed": String(state.filter.assignee === u.id),
        title: `Показать задачи: ${u.display_name}`,
        onclick: () => pick(u.id),
      }, magnet(u), h("span", { class: "chip-name" }, u.display_name)),
    ),
  );
}

function renderCategoryFilter() {
  const cats = [...new Set(state.tasks.map((t) => t.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
  if (state.filter.category && !cats.includes(state.filter.category)) state.filter.category = "";
  $("#category-filter").replaceChildren(
    h("option", { value: "" }, "Все категории"),
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
const STATUS_ORDER = Object.fromEntries(COLUMNS.map((c, i) => [c.id, i]));

function childrenOf(id) {
  return state.tasks
    .filter((t) => t.parent_id === id)
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.position - b.position);
}

// Незакрытые задачи, которых эта ждёт.
function openBlockers(task) {
  return task.links
    .filter((l) => l.type === "blocked_by")
    .map((l) => taskById(l.task_id))
    .filter((b) => b && b.status !== "done");
}

// Правила «предупреждать, а не запрещать»: возвращает текст вопроса или null.
function statusWarning(task, status) {
  if (!task || status === task.status) return null;
  const lines = [];
  if (status === "done") {
    const open = childrenOf(task.id).filter((c) => c.status !== "done");
    if (open.length) lines.push(`Не сделаны подзадачи: ${open.map((c) => `«${c.title}»`).join(", ")}.`);
  }
  if (status !== "todo") {
    const blockers = openBlockers(task);
    if (blockers.length) lines.push(`Задача ждёт: ${blockers.map((b) => `«${b.title}»`).join(", ")}.`);
  }
  return lines.length ? `${lines.join("\n")}\n\nВсё равно перенести «${task.title}» в ${COLUMN_TITLE[status]}?` : null;
}

function columnTasks(status) {
  return state.tasks
    .filter((t) => t.status === status)
    .sort((a, b) => a.position - b.position || a.id - b.id);
}

function isOldDone(t) {
  const done = utcToLocal(t.completed_at);
  return done && Date.now() - done.getTime() > DONE_RECENT_DAYS * 86400000;
}

function renderBoard() {
  const board = $("#board");
  board.replaceChildren(
    ...COLUMNS.map((col) => {
      let tasks = columnTasks(col.id).filter(matchesFilter);
      let hiddenOld = 0;
      if (col.id === "done" && !state.showOldDone) {
        const recent = tasks.filter((t) => !isOldDone(t));
        hiddenOld = tasks.length - recent.length;
        tasks = recent;
      }
      const list = h("div", { class: "cards", "data-status": col.id },
        tasks.length ? tasks.map(renderCard) : h("p", { class: "empty-col" }, col.empty));
      wireDropZone(list);
      return h("section", { class: "column", "aria-label": col.title },
        h("div", { class: "column-head" },
          h("h2", {}, col.title),
          h("span", { class: "count" }, String(tasks.length)),
          h("button", { class: "add", title: `Добавить в ${col.title}`, "aria-label": `Добавить в ${col.title}`, onclick: () => openTask(null, col.id) }, "+"),
        ),
        list,
        hiddenOld ? h("button", { class: "show-old", onclick: () => { state.showOldDone = true; renderBoard(); } },
          `Показать старые (${hiddenOld})`) : null,
      );
    }),
  );
}

function renderCard(t) {
  const meta = [];
  const done = t.status === "done";
  if (t.priority === "high" && !done) meta.push(h("span", { class: "tag urgent" }, "Срочно"));
  if (t.due_date && !done) {
    const d = dueInfo(t.due_date);
    meta.push(h("span", { class: `tag ${d.cls}` }, d.text));
  }
  if (t.checklist.length) {
    const ready = t.checklist.filter((i) => i.done).length;
    meta.push(h("span", { class: `tag ${ready === t.checklist.length ? "complete" : ""}`, title: "Чек-лист" },
      `☑ ${ready}/${t.checklist.length}`));
  }
  const children = childrenOf(t.id);
  if (children.length) {
    const ready = children.filter((c) => c.status === "done").length;
    meta.push(h("span", { class: `tag ${ready === children.length ? "complete" : ""}`, title: "Подзадачи" },
      `▤ ${ready}/${children.length}`));
  }
  const blockers = done ? [] : openBlockers(t);
  if (blockers.length) {
    const more = blockers.length > 1 ? ` и ещё ${blockers.length - 1}` : "";
    meta.push(h("span", { class: "tag wait", title: blockers.map((b) => b.title).join(", ") },
      `⏳ ждёт: ${blockers[0].title}${more}`));
  }
  if (t.recurrence !== "none") meta.push(h("span", { class: "tag", title: "Повторяется" }, `↻ ${RECURRENCE[t.recurrence]}`));
  if (t.category) meta.push(h("span", { class: "tag" }, t.category));
  // Очки — последним элементом строки меток: в углу карточки они наезжали на магнит.
  if (t.points > 1) {
    meta.push(h("span", { class: "points", title: `${t.points} ${plural(t.points, "очко", "очка", "очков")} за выполнение` }, `+${t.points}`));
  }

  const card = h("article", {
    class: `card ${t.priority} ${done ? "done" : ""}`,
    draggable: "true",
    tabindex: "0",
    "data-id": t.id,
    onclick: () => openTask(t),
    onkeydown: (e) => { if (e.key === "Enter") openTask(t); },
    ondragstart: (e) => {
      state.dragId = t.id;
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", String(t.id));
      requestAnimationFrame(() => card.classList.add("dragging"));
    },
    ondragend: () => {
      state.dragId = null;
      card.classList.remove("dragging");
      document.querySelectorAll(".drop-marker").forEach((m) => m.remove());
    },
  },
    magnet(userById(t.assignee_id)),
    t.parent_id && taskById(t.parent_id)
      ? h("div", { class: "parent-ref", title: "Входит в задачу" }, `↳ ${taskById(t.parent_id).title}`) : null,
    h("div", { class: "title" }, t.title),
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
    const status = list.dataset.status;
    const before = cardAfterPointer(list, e.clientY);
    // Индекс считаем по полной колонке, а не только по видимым после фильтра карточкам.
    const full = columnTasks(status).map((t) => t.id).filter((x) => x !== id);
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
    const warning = statusWarning(task, status);
    if (warning && !confirm(warning)) {
      renderBoard();
      return;
    }
    const wasDone = task.status === "done";
    // Оптимистично переставляем локально, потом сверяемся с сервером.
    full.splice(index, 0, id);
    task.status = status;
    full.forEach((tid, pos) => { state.tasks.find((t) => t.id === tid).position = pos; });
    renderBoard();
    try {
      await api("POST", `/api/tasks/${id}/move`, { status, index });
      if (status === "done" && !wasDone) celebrate(task);
    } catch (err) {
      toast(err.message);
    }
    loadAll();
  });
}

function celebrate(task) {
  const parts = [];
  if (task.assignee_id && task.points) {
    parts.push(`${userName(task.assignee_id)}: +${task.points} ${plural(task.points, "очко", "очка", "очков")}`);
  }
  if (task.recurrence !== "none") parts.push("следующий повтор добавлен в To Do");
  if (parts.length) toast(parts.join(", "));
}

// ---------- карточка задачи ----------

const taskDialog = $("#task-dialog");
const taskForm = $("#task-form");

function openTask(task, status = "todo", parentId = null) {
  state.editingTask = task;
  $("#task-error").textContent = "";
  $("#task-dialog-title").textContent = task ? "Задача" : "Новая задача";
  $("#task-save").textContent = task ? "Сохранить" : "Добавить";
  $("#task-delete").hidden = !task;
  $("#activity").hidden = !task;
  taskDialog.classList.toggle("wide", Boolean(task));

  taskForm.assignee_id.replaceChildren(
    h("option", { value: "" }, "Никто"),
    ...state.users.map((u) => h("option", { value: u.id }, u.display_name)),
  );
  taskForm.status.replaceChildren(...COLUMNS.map((c) => h("option", { value: c.id }, c.title)));

  const t = task || {
    title: "", description: "", status, priority: "normal", category: state.filter.category,
    assignee_id: state.filter.assignee ?? state.me.id, due_date: null, recurrence: "none", points: 1,
    parent_id: parentId,
  };
  taskForm.title.value = t.title;
  taskForm.description.value = t.description;
  taskForm.assignee_id.value = t.assignee_id ?? "";
  taskForm.status.value = t.status;
  taskForm.due_date.value = t.due_date ?? "";
  taskForm.recurrence.value = t.recurrence;
  taskForm.category.value = t.category;
  ensurePointsOption(t.points);
  taskForm.points.value = String(t.points);
  taskForm.querySelector(`input[name="priority"][value="${t.priority}"]`).checked = true;

  fillParentSelect(task, t.parent_id ?? null);
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
    status: f.status.value,
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
  const warning = statusWarning(task, body.status);
  if (warning && !confirm(warning)) return;
  try {
    if (task) {
      await api("PATCH", `/api/tasks/${task.id}`, body);
      if (task.status !== "done" && body.status === "done") celebrate({ ...task, ...body });
    } else {
      await api("POST", "/api/tasks", { ...body, checklist: state.draftChecklist.map((i) => i.text) });
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
  const warn = task.status === "done" && task.points ? " Очки за неё пропадут из рейтинга." : "";
  if (!confirm(`Удалить задачу «${task.title}»?${warn}`)) return;
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
    .sort((a, b) => a.title.localeCompare(b.title, "ru"));
  select.replaceChildren(
    h("option", { value: "" }, "Ни в какую, самостоятельная задача"),
    ...candidates.map((t) => h("option", { value: t.id }, t.title)),
  );
  select.value = parentId ?? "";
  select.disabled = hasChildren;
  select.title = hasChildren ? "У задачи есть свои подзадачи, поэтому сама она подзадачей быть не может" : "";
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
    h("span", { class: `status-pill s-${task.status}` }, COLUMN_TITLE[task.status]),
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
    $("#subtasks-progress").textContent = children.length ? `${ready} из ${children.length}` : "";
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
        before: h("span", { class: `link-type ${l.type}` }, LINK_TYPES[l.type].label),
        after: h("button", {
          type: "button", class: "icon-btn", "aria-label": `Убрать связь с «${other.title}»`,
          onclick: () => removeLink(l.id),
        }, "×"),
      });
    }),
  );
  const linked = new Set(task.links.map((l) => l.task_id));
  const targets = state.tasks
    .filter((t) => t.id !== task.id && !linked.has(t.id) && !isOldDone(t))
    .sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || a.title.localeCompare(b.title, "ru"));
  $("#link-target").replaceChildren(
    h("option", { value: "" }, "Выберите задачу"),
    ...COLUMNS.map((c) => {
      const group = targets.filter((t) => t.status === c.id);
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
  $("#checklist-progress").textContent = items.length ? `${ready} из ${items.length}` : "";
  $("#checklist-items").replaceChildren(
    ...items.map((item) =>
      h("li", { class: item.done ? "done" : "" },
        h("label", {},
          h("input", { type: "checkbox", checked: item.done, onchange: (e) => toggleItem(item, e.target.checked) }),
          h("span", {}, item.text),
        ),
        h("button", { type: "button", class: "icon-btn", "aria-label": `Удалить пункт «${item.text}»`, onclick: () => removeItem(item) }, "×"),
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
      return p.recurring_from ? "создаёт следующий повтор" : "создаёт задачу";
    case "status":
      return `переносит из ${COLUMN_TITLE[p.from]} в ${COLUMN_TITLE[p.to]}`;
    case "assigned":
      return p.to ? `назначает исполнителем: ${userName(p.to)}` : "снимает исполнителя";
    case "edited":
      return `меняет ${p.fields.map((f) => FIELD_NAMES[f] || f).join(", ")}`;
    case "linked":
      return `связывает: ${LINK_TYPES[p.type]?.phrase ?? p.type} «${taskById(p.other)?.title ?? "удалённая задача"}»`;
    case "unlinked":
      return `убирает связь: ${LINK_TYPES[p.type]?.phrase ?? p.type} «${taskById(p.other)?.title ?? "удалённая задача"}»`;
    default:
      return e.kind;
  }
}

function renderEvent(e) {
  const author = userById(e.user_id);
  const when = h("time", { datetime: e.created_at }, fmtStamp.format(utcToLocal(e.created_at)));
  const name = h("b", {}, author ? author.display_name : "Удалённый пользователь");
  if (e.kind !== "comment") {
    return h("li", { class: "event" }, name, " ", describeEvent(e), h("span", { class: "muted" }, ", "), when);
  }
  const canDelete = e.user_id === state.me.id || state.me.is_admin;
  return h("li", { class: "comment" },
    magnet(author),
    h("div", { class: "bubble" },
      h("div", { class: "bubble-head" }, name, when,
        canDelete ? h("button", { type: "button", class: "link-btn", onclick: () => deleteComment(e) }, "удалить") : null),
      h("p", {}, e.payload.text),
    ),
  );
}

async function deleteComment(e) {
  if (!confirm("Удалить комментарий?")) return;
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
  const rows = await api("GET", `/api/leaderboard?period=${state.ratingPeriod}`);
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
            h("span", { class: "score" }, `${r.points} ${plural(r.points, "очко", "очка", "очков")}`),
          ),
          h("div", { class: "bar" }, h("span", { style: `width:${(r.points / max) * 100}%;--c:${u?.color}` })),
          h("div", { class: "muted small" }, `Выполнено задач: ${r.done}`),
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
    ...THEMES.map((t) =>
      h("button", {
        type: "button", class: "theme-tile", role: "radio", "aria-checked": String(t.id === active),
        onclick: () => chooseTheme(t.id),
      },
        h("span", { class: "theme-preview", "data-theme": t.id, "aria-hidden": "true" },
          h("span", { class: "pv-col" }, h("span", { class: "pv-card" }), h("span", { class: "pv-card urgent short" })),
          h("span", { class: "pv-col" }, h("span", { class: "pv-card accent" })),
          h("span", { class: "pv-col" }, h("span", { class: "pv-card short" })),
        ),
        h("span", { class: "theme-name" }, t.name),
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
    toast(`Тема не сохранилась: ${err.message}`);
  } finally {
    themeSaving = false;
  }
}

$("#open-theme").addEventListener("click", () => {
  renderThemes();
  themeDialog.showModal();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateThemeColor);
updateThemeColor();

// ---------- люди ----------

const peopleDialog = $("#people-dialog");
const userForm = $("#user-form");
let pickedColor = COLORS[0];

function renderSwatches() {
  $("#swatches").replaceChildren(
    ...COLORS.map((c) =>
      h("button", {
        type: "button", role: "radio", "aria-checked": String(c === pickedColor), "aria-label": `Цвет ${c}`,
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
        "Уведомления в Telegram выключены. Чтобы включить, создайте бота у @BotFather и запустите сервер с переменной TELEGRAM_BOT_TOKEN — подробности в README."),
    );
    return;
  }
  box.hidden = false;
  if (state.me.telegram_linked) {
    box.replaceChildren(
      h("p", {}, "Уведомления приходят вам в Telegram."),
      h("button", { type: "button", class: "btn ghost", onclick: unlinkTelegram }, "Отключить"),
    );
  } else {
    box.replaceChildren(
      h("p", {}, "Получайте в Telegram новые задачи, комментарии и утреннюю сводку."),
      h("button", { type: "button", class: "btn primary", onclick: linkTelegram }, "Подключить Telegram"),
    );
  }
}

async function linkTelegram() {
  try {
    const { url } = await api("POST", "/api/me/telegram");
    window.open(url, "_blank", "noopener");
    $("#telegram-box").replaceChildren(
      h("p", {}, "В открывшемся чате нажмите «Start». Когда вернётесь сюда, статус обновится."),
      h("a", { href: url, target: "_blank", rel: "noopener", class: "btn ghost" }, "Открыть бота ещё раз"),
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
      const meta = [`В работе: ${s.open}`, `сделано за неделю: ${s.done_week}`];
      if (state.config.telegram && u.telegram_linked) meta.push("Telegram подключён");
      if (isAdmin && u.google_email) meta.push(`Google: ${u.google_email}`);
      return h("li", {},
        magnet(u, "lg"),
        h("div", { class: "who" },
          h("b", {}, u.display_name), " ",
          h("span", { class: "muted small" }, `@${u.username}${u.is_admin ? ", админ" : ""}`),
          h("div", { class: "muted small" }, meta.join(", ")),
        ),
        h("div", { class: "row-actions" },
          canEdit ? h("button", { class: "btn ghost", onclick: () => editUser(u) }, "Изменить") : null,
          isAdmin && u.id !== state.me.id
            ? h("button", { class: "btn danger-ghost", onclick: () => deleteUser(u) }, "Удалить") : null,
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
  $("#user-form-title").textContent = "Добавить человека";
  $("#user-save").textContent = "Добавить";
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
  $("#user-form-title").textContent = u.id === state.me.id ? "Мой профиль" : `Изменить: ${u.display_name}`;
  $("#user-save").textContent = "Сохранить";
  $("#user-cancel").hidden = false;
  $("#user-username-field").hidden = true;
  $("#user-admin-field").hidden = !state.me.is_admin || u.id === state.me.id;
  // Google-почту назначает только администратор, в том числе себе.
  $("#user-google-field").hidden = !state.me.is_admin;
  userForm.username.required = false;
  userForm.password.required = false;
  userForm.password.value = "";
  userForm.password.placeholder = "Оставьте пустым, чтобы не менять";
  userForm.display_name.value = u.display_name;
  userForm.is_admin.checked = u.is_admin;
  userForm.google_email.value = u.google_email ?? "";
  pickedColor = u.color;
  $("#user-error").textContent = "";
  renderSwatches();
  userForm.display_name.focus();
}

async function deleteUser(u) {
  if (!confirm(`Удалить ${u.display_name}? Его задачи останутся без исполнителя.`)) return;
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
