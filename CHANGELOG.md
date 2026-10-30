# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

Every release ships as a container image `ghcr.io/gera-corp/to-do-board:<version>`
and a Helm chart `oci://ghcr.io/gera-corp/charts/to-do-board` of the same version.
The GitHub release notes are taken from the matching section below, so add an entry
here before pushing a `vX.Y.Z` tag.

## [0.7.0] - 2026-10-30

### Added
- Several boards: an administrator creates, renames and deletes them, and everyone
  switches between them in the header. All people see all boards.
- Custom columns per board: add, rename, reorder and delete. Each column has a role
  (Start, In progress, Review, Done) that drives the behaviour: repeats land in Start,
  Review notifies the author, Done closes the task and awards points.
- A Settings window for administrators with the app name, boards and columns.
- Points and the leaderboard can be turned off per board.

### Changed
- The app is no longer about household chores: new installations are called
  "Task Board" / "Доска задач". Existing installations keep their name and board.
- The leaderboard and history use board-specific data; history shows column names as
  they were at the time of the move.
- `POST /api/tasks/{id}/move` and `PATCH /api/tasks/{id}` take `column_id`; `status` still
  works and means the first column with that role.

### Security
- A column with tasks can't be deleted without choosing where the tasks go, and a board
  always keeps a Start and a Done column.

## [0.6.0] - 2026-10-23

### Added
- English and Russian interface, English by default. Until someone picks a language,
  the board follows the browser: Russian for a Russian browser, English otherwise.
- Language choice in Theme → Appearance, saved in the user profile and synced across
  devices, plus an English | Русский switch on the sign-in page.
- API errors, Telegram notifications, bot replies and the morning digest arrive in the
  language each person uses the board in.
- The installed app's name follows the browser language ("Our Home" / "Наш дом").
- Releases on GitHub with notes from this changelog, published automatically by CI.

### Changed
- People who used the board before this release keep Russian notifications.

## [0.5.0] - 2026-10-23

### Added
- Subtasks, one level deep. A subtask is a regular card with its own assignee,
  column, due date and points, labelled with its parent ("↳ Kids' room renovation").
  The parent card shows progress ("▤ 1/3"), and subtasks can be added and opened
  straight from the parent's dialog.
- Links between tasks: "waits for" / "blocks" and "related to". Cards show what a
  task is waiting for ("⏳ waits for: Buy paint").
- Telegram notification "You can start" for the assignee once everything a task was
  waiting for is done.
- Soft rules: the board asks for confirmation before starting a task that is still
  waiting for something, or closing a task with unfinished subtasks.

### Changed
- Deleting a parent task keeps its subtasks as standalone tasks.
- Links and unlinks are recorded in the history of both tasks.

### Security
- Blocking cycles ("A waits for B, B waits for A") are rejected by the server.

## [0.4.0] - 2026-10-18

### Added
- Sign in with Google (OpenID Connect with PKCE, a browser-bound `state` and `nonce`).
  Only people an administrator has added and assigned a Google address to can sign in;
  the Google account id is remembered on first sign-in.
- `config.passwordLogin` to turn password sign-in off once everyone uses Google.
- Helm values `config.googleClientId` and `config.trustedProxies`; the client secret
  comes from Vault via `vault.env`.

### Security
- Password sign-in is rate limited: 10 failed attempts per account or 30 per address
  lock it for 15 minutes.
- Response time no longer reveals whether a username exists.
- The client address is taken from `X-Forwarded-For` only from trusted proxies.

## [0.3.0] - 2026-10-16

### Added
- Installable app (PWA): manifest, icons including a maskable one, an "Install" button
  where the browser supports it.
- Offline mode: the last loaded board is shown read-only with a "No connection" banner;
  changes are refused with a clear message instead of being lost.
- Automatic updates: a new release replaces the cached app as a whole and reloads the
  page, but never in the middle of editing a task.

### Security
- Cached board data is wiped from the device on sign-out.

## [0.2.2] - 2026-10-16

### Fixed
- The points badge no longer overlaps the assignee magnet on single-line cards.
- The category filter takes the full row on phones instead of shrinking to a stub.

## [0.2.1] - 2026-10-16

### Added
- The Helm chart is published to GHCR as an OCI artifact, and only after the image of
  the same version has been built and scanned. Deployed through ArgoCD.

### Changed
- Chart and image versions come from the git tag.

### Security
- The database volume is protected from deletion by ArgoCD (`Delete=false`) as well as
  by `helm uninstall`.

## [0.2.0] - 2026-10-16

### Added
- Seven themes: system, Enamel, Night, Mint, Kraft, Midnight and High contrast. The
  choice is stored in the user profile and follows them across devices.

## [0.1.0] - 2026-10-16

### Added
- Kanban board with To Do, In Progress, Review and Done columns, drag and drop and
  persistent ordering.
- Users with password sign-in; an administrator manages people and permissions.
- Tasks with assignee, due date, priority, category and points; daily, weekly and
  monthly recurring tasks.
- Checklists, task history and comments.
- Points and a weekly, monthly and all-time leaderboard.
- Telegram bot: assignment, review, completion and comment notifications, a morning
  digest, `/tasks` and `/stop`.
- Filters by person and category, search, auto refresh, dark theme, mobile layout.
- Distroless non-root container image for amd64 and arm64, CI with tests, linters,
  SBOM, provenance and Trivy scanning, and a Helm chart with secrets from Vault.

[0.7.0]: https://github.com/gera-corp/to-do-board/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/gera-corp/to-do-board/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/gera-corp/to-do-board/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/gera-corp/to-do-board/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/gera-corp/to-do-board/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/gera-corp/to-do-board/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/gera-corp/to-do-board/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/gera-corp/to-do-board/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/gera-corp/to-do-board/releases/tag/v0.1.0
