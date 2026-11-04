# Task Board — kanban boards for any kind of work

English | [Русский](README.ru.md)

A self-hosted kanban for a team, a family or any small group. Create several boards, give each its own columns for your process, and assign tasks to people, who show up as coloured magnets. Watch the tasks you care about and hear about every change in Telegram or by email.

## Getting started

```bash
./run.sh
```

Open http://localhost:8000. On the first visit the app asks you to create the first user, who becomes the administrator and can add everyone else under People.

The server listens on all interfaces, so a phone on the same Wi‑Fi can open the board at `http://<computer-IP>:8000`.

### With Docker Compose

```bash
docker compose up -d
```

This downloads the published image and runs it with the same restrictions as in the cluster: a read-only filesystem, no Linux capabilities, no root. Open http://localhost:8000. `.env` is optional and is picked up automatically; `PORT` and `HOST` set the published port and address.

| Command | What it runs |
|---|---|
| `docker compose up -d` | the published image, `latest` by default |
| `TAG=0.7.2 docker compose up -d` | a specific published version |
| `docker compose up -d --build` | your local code, built from the source |
| `docker compose pull && docker compose up -d` | the newest published image |

An image that is already on the machine doesn't update by itself. A local build also replaces the image under the same name, so `docker compose pull` is the way back to the published one.

The database lives in the `data` Docker volume, separate from the `data/` folder that `./run.sh` uses. It survives `docker compose down` and rebuilds; `docker compose down -v` deletes it.

Logs: `docker compose logs -f`.

A Telegram bot token can only serve one running copy. If `.env` holds the token of a bot that already runs elsewhere, the two copies will steal each other's messages, so use a separate test bot or leave the token empty.

### Settings

Settings live in a `.env` file in the project root. Start from the template:

```bash
cp .env.example .env
```

`.env` is listed in `.gitignore`, so tokens never reach the repository. Any setting can also come from an environment variable, which takes precedence over the file. Changes to `.env` apply after a server restart.

| Variable | Default | Purpose |
|---|---|---|
| `TODO_DB` | `data/tasks.db` | path to the SQLite file |
| `HOST` | `0.0.0.0` | listen address |
| `PORT` | `8000` | port |
| `TELEGRAM_BOT_TOKEN` | — | bot token; without it Telegram notifications are off |
| `DIGEST_HOUR` | `9` | hour of the morning digest in Telegram and by email (server time); the old name `TELEGRAM_DIGEST_HOUR` still works |
| `TODO_PUBLIC_URL` | — | board address: links in messages and the return address after signing in with Google |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | — | Google OAuth client; without them sign-in with Google is off |
| `TODO_PASSWORD_LOGIN` | `true` | password sign-in |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | proxies whose `X-Forwarded-For` header is trusted |
| `TODO_ALLOWED_NETWORKS` | — | addresses and subnets allowed to open the board, separated by commas; empty means everyone. Everyone else gets 403 |
| `LDAP_URL` and other `LDAP_*` | — | sign-in through LDAP or Active Directory, see below |
| `SMTP_HOST` and other `SMTP_*` | — | email notifications, see [Notifications](#notifications) |

`TODO_ALLOWED_NETWORKS` is for a gateway that can't filter addresses itself. Behind a proxy, name it exactly in `FORWARDED_ALLOW_IPS`: the board refuses to start with `*`, and the proxy's own address is never allowed, so the board closes if the proxy stops passing the client address. A mistake in the list stops the start too. `/api/health` always answers.

## Features

- **Several boards.** An administrator creates boards, and everyone switches between them in the header. Tasks, categories and the leaderboard belong to a board.
- **Your own columns.** Add, rename, reorder and delete columns per board under Settings. Each column has a role that sets its behaviour: new tasks and repeats land in Start, In progress is for work under way, Review notifies the author, Done closes the task and awards points. A board always keeps at least one Start and one Done column; deleting a column with tasks asks where to move them. Cards are dragged between and within columns, and the order is kept.
- **Settings for administrators:** the app name, boards, columns, and points on or off per board.
- **People.** Sign-in with a username and password, or with Google. An administrator adds and removes people and grants administrator rights. Everyone can change their own name, colour and password, and set up their own notifications: Telegram and an email address only they can see.
- **Tasks** with an assignee, due date, priority ("can wait", "normal", "urgent"), category and description.
- **Recurring chores** (daily, weekly or monthly). When one reaches Done, the next one appears in To Do with the due date moved forward.
- **Due dates on cards.** Tasks due today are highlighted in yellow, overdue ones in red.
- **Filters** by person and category, plus text search.
- **Checklists inside a task**, such as a shopping list. Progress shows on the card. A recurring task carries its checklist over to the next repeat, unticked.
- **Subtasks.** Split a big task into parts with their own assignees, due dates and points: "Kids' room renovation" → "Buy paint", "Paint the walls". Subtasks are regular cards labelled "↳ Kids' room renovation", and the parent shows progress ("▤ 1/3"). One level only: a subtask has no subtasks of its own. Deleting the parent turns its subtasks into standalone tasks.
- **Links between tasks:** "waits for" / "blocks" and plain "related to". Cards show what a task is waiting for ("⏳ waits for: Buy paint"). When the last task it waited for reaches Done, its watchers get "You can start". Cycles ("A waits for B, B waits for A") are rejected. The rules are soft: the board warns before starting a task that is still waiting, or closing one with unfinished subtasks, but doesn't forbid it.
- **History and comments.** A task shows who created, moved, reassigned and changed it, with a conversation next to it.
- **Watching tasks**, as in Jira. The eye in a task's dialog shows how many people watch it; click it to start or stop watching. The author, the assignee and everyone who comments watch automatically. Watchers hear about everything that happens to the task in Telegram, by email or both.
- **Points and leaderboard** (optional, per board). Each task is worth 1 to 8 points, which go to the assignee when it reaches Done. The leaderboard covers the week, the month and all time.
- **Stats** under People: open tasks and tasks done this week per person.
- **A tidy Done column.** Tasks finished more than 14 days ago hide behind "Show older".
- **English and Russian.** The interface is English by default. Until someone picks a language in their profile (Theme → Language), the board follows the browser: a Russian browser gets the Russian board. The choice is saved in the profile and follows the person across devices. Errors, notifications in Telegram and by email, and the morning digest come in the same language.
- **Seven themes**, from the system light/dark theme to high contrast, saved in the profile.
- **Installable app (PWA).** Add the board to the home screen: on Android with the Install button or the browser menu, on iPhone with Share → Add to Home Screen. It opens without the address bar and with its own icon. Offline, it shows the last loaded board read-only; signing out wipes the saved data from the device. New versions arrive on their own and reload the page, but never in the middle of editing a task.
- **Auto refresh.** The board refreshes every 30 seconds and when you return to the tab, so changes by others show up without reloading.
- Mobile layout: columns scroll sideways, and a task's column can be changed in its dialog.

The database is upgraded automatically on start: the schema keeps its version number and applies only the missing migrations.

## Sign in with Google

Sign-in with Google complements passwords. Only people an administrator has already added under People, with their Google address filled in, can sign in; nobody can register on their own. The Google account id is remembered on first sign-in, so it keeps working even if the person later changes their Google address.

Setup:

1. In the [Google Cloud Console](https://console.cloud.google.com/), open Google Auth Platform. Under **Audience**, the user type should be External. In **Testing** mode only the addresses listed under Test users can sign in, which is a good extra filter for a family board; **Publish app** opens Google sign-in to everyone (the board itself still lets in only the people you added). The basic `openid`, `email` and `profile` scopes need no verification by Google.
2. Under **Clients**, create a client of type Web application and add this Authorized redirect URI, exactly as written, with no trailing slash:
   ```
   https://todo.geracorp.org/api/auth/google/callback
   ```
   The client secret is shown only once, so copy it right away.
3. The client ID is not a secret and goes into `config.googleClientId`. Put the client secret into Vault and add it to `vault.env`:
   ```bash
   vault kv patch secret/to-do-board GOOGLE_CLIENT_SECRET=...
   ```
4. Under People, the administrator fills in each person's Google address.
5. Once everyone uses Google, password sign-in can be turned off with `config.passwordLogin: false`. The first administrator is still created with a password.

To run locally, set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `TODO_PUBLIC_URL=http://localhost:8000` in `.env`, and add a second redirect URI in the console: `http://localhost:8000/api/auth/google/callback`.

Password sign-in is protected against guessing: after 10 failed attempts on one username or 30 from one address it is locked for 15 minutes. A non-existent username takes as long to answer as a real one, so usernames can't be discovered by timing.

## Sign in with LDAP or Active Directory

The usual sign-in form works with a directory, too: the board finds the person with a read-only service account, then checks their password against the directory. The password is never stored on the board. Set `LDAP_URL` to turn this on.

- **Who can sign in:** whoever matches `LDAP_USER_FILTER`, so put the group into the filter. On first sign-in the person is created on the board as a regular user.
- **Administrators** are assigned on the board. Create the first one with a local password: this account keeps working when the directory is down.
- **Existing people** with the same username aren't linked automatically, so that a directory user named `admin` can't become your local administrator. Set `LDAP_LINK_EXISTING=true` if the usernames are known to match. After linking, the local password stops working.
- **Removing someone:** a person disabled in the directory can't sign in again, but a session that is already open lasts up to 90 days. Delete them under People to end it right away.
- **Account lockout:** failed attempts on the board also count in the directory, so an AD lockout policy can apply.

Always use `ldaps://` outside a trusted network. The certificate is verified by default, including the server name, so connect by the name in the certificate rather than by IP. For an internal CA or a self-signed certificate, put the root or the self-signed certificate into `./certs` and set `LDAP_CA_FILE`; in the chart, use a Secret with the `ca.pem` key and `config.ldap.caSecret`.

`LDAP_TLS_INSECURE=true` (`config.ldap.tlsInsecure`) turns the verification off. The traffic stays encrypted, but anyone who can impersonate the server gets the passwords, so prefer `LDAP_CA_FILE`, which works with self-signed certificates too.

LLDAP:

```
LDAP_URL=ldaps://lldap.example.org:6360
LDAP_BASE_DN=dc=example,dc=org
LDAP_BIND_DN=uid=reader,ou=people,dc=example,dc=org
LDAP_BIND_PASSWORD=...
LDAP_USER_FILTER=(&(uid={username})(memberOf=cn=board_users,ou=groups,dc=example,dc=org))
LDAP_CA_FILE=/certs/ca.pem
```

Active Directory, with active members of a group, including nested groups:

```
LDAP_URL=ldaps://dc01.example.local:636
LDAP_BASE_DN=DC=example,DC=local
LDAP_BIND_DN=CN=svc-board,OU=Service Accounts,DC=example,DC=local
LDAP_BIND_PASSWORD=...
LDAP_USER_FILTER=(&(objectClass=user)(sAMAccountName={username})(!(userAccountControl:1.2.840.113556.1.4.803:=2))(memberOf:1.2.840.113556.1.4.1941:=CN=board_users,OU=Groups,DC=example,DC=local))
LDAP_LOGIN_ATTR=sAMAccountName
LDAP_CA_FILE=/certs/ca.pem
```

Export the root certificate of an AD CA on the domain controller and convert it to PEM:

```
certutil -ca.cert ca.cer
certutil -encode ca.cer ca.pem
```

## Notifications

Watchers of a task (see Features) hear about everything that happens to it: comments, moves between columns, a new assignee, "You can start", field changes ("Due date: 5 Nov → 7 Nov"), checklist items, subtasks, links and deletion. Comments, assignments, moves, "You can start" and deletion arrive at once. Smaller changes are collected for 2 minutes and arrive as one message, so ticking ten checklist items is one message, not ten; a field changed and changed back in that time is not mentioned. You don't get notified about your own actions.

Telegram and email get the same notifications. Undelivered ones wait in a queue in the database and are retried for up to 24 hours, so a restart or a mail server outage loses nothing. Links in messages open the task itself when `TODO_PUBLIC_URL` is set.

### Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) using `/newbot` and copy the token.
2. Put the token into `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   ```
   Then restart `./run.sh`.
3. Each person opens People, clicks Connect Telegram and presses Start in the chat with the bot.

Bot commands: `/tasks` lists your open tasks, `/stop` turns Telegram notifications off. The bot also sends a morning digest: what's due today, what's overdue and what's due tomorrow.

### Email

Point the board at your mail server in `.env`:

```
SMTP_HOST=mail.example.org
SMTP_FROM=board@example.org
SMTP_USER=board@example.org
SMTP_PASSWORD=...
```

| Variable | Default | Purpose |
|---|---|---|
| `SMTP_HOST` | — | mail server; without it email is off and hidden in the interface |
| `SMTP_PORT` | `587`, `465` for `ssl` | port |
| `SMTP_SECURITY` | `starttls` | `starttls`, `ssl` or `none` |
| `SMTP_USER`, `SMTP_PASSWORD` | — | sign-in, if the server needs it |
| `SMTP_FROM` | — | sender address, required |
| `SMTP_CA_FILE` | — | root certificate of an internal CA; with Docker Compose put it into `./certs` and use `/certs/ca.pem` |

The server certificate is always verified. Each person enters their own address in People: the board sends a confirmation link, valid for 24 hours, and nothing else goes to the address until it is confirmed. The address is visible only to its owner, not to other people or administrators. In the same place you can turn email notifications off and turn on the morning digest by email (off by default).

## Project layout

```
app/
  main.py        FastAPI: the API and static files
  db.py          SQLite schema and migrations
  security.py    password hashing (PBKDF2), session tokens, sign-in rate limiting
  google.py      sign-in with Google (OpenID Connect)
  ldap_auth.py   sign-in through LDAP or Active Directory
  telegram.py    the Telegram bot and its commands
  notify.py      watchers, notification texts and the morning digest
  outbox.py      notification queue: grouping, delivery, retries
  mailer.py      sending email over SMTP
  i18n.py        English translations of server messages
  recurrence.py  next due date for recurring tasks
  config.py      reading .env
  __main__.py    server entry point: python -m app
  static/        plain JavaScript frontend, no build step
tests/           pytest tests
charts/          Helm chart
Dockerfile       container image (distroless, non-root)
compose.yaml     run with Docker Compose
.github/         CI: tests, linters, image build and scanning, releases
```

### API

All endpoints except sign-in, first-time setup and health require the session cookie.

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/setup` | check whether the first user is needed and which sign-in methods are on, or create that user |
| POST | `/api/login`, `/api/logout` | sign in and out |
| GET | `/api/auth/google/start`, `/api/auth/google/callback` | sign in with Google |
| GET | `/api/me` | the current user, with their own email settings |
| PUT/DELETE | `/api/me/email` | send a confirmation link to a new address, or remove the address |
| POST | `/api/email/confirm` | confirm an address with `{token}` from the link in the email (the link opens the board, which asks before confirming, so mail scanners that open links confirm nothing) |
| PATCH | `/api/me/notifications` | `email_notify`, `email_digest` |
| GET/POST | `/api/users` | list people, add a person (administrator only) |
| PATCH/DELETE | `/api/users/{id}` | change or delete a person |
| GET/POST | `/api/boards` | list boards with their columns, create a board (administrator) |
| PATCH/DELETE | `/api/boards/{id}` | change or delete a board (administrator) |
| POST | `/api/boards/{id}/columns` | add a column (administrator) |
| PUT | `/api/boards/{id}/columns/order` | reorder columns (administrator) |
| PATCH/DELETE | `/api/columns/{id}` | change or delete a column; `?move_to=` says where its tasks go (administrator) |
| PUT | `/api/settings` | set the app name (administrator) |
| GET/POST | `/api/tasks` | list tasks (filters `board_id`, `assignee_id`, `category`, `q`) and create one |
| GET/PATCH/DELETE | `/api/tasks/{id}` | a single task |
| POST | `/api/tasks/{id}/move` | move `{column_id, index}`; `status` means the first column with that role |
| POST | `/api/tasks/{id}/links` | link tasks (`blocks`, `blocked_by`, `relates`) |
| DELETE | `/api/links/{id}` | remove a link |
| POST | `/api/tasks/{id}/checklist` | add a checklist item |
| PATCH/DELETE | `/api/checklist/{id}` | change or delete an item |
| GET | `/api/tasks/{id}/events` | history and comments |
| POST/DELETE | `/api/tasks/{id}/watch` | start or stop watching a task |
| POST | `/api/tasks/{id}/comments` | add a comment |
| DELETE | `/api/events/{id}` | delete a comment (its author or an administrator) |
| GET | `/api/leaderboard?period=week\|month\|all&board_id=` | points leaderboard |
| GET | `/api/config` | whether Telegram and email are on, and the app name |
| POST/DELETE | `/api/me/telegram` | get a link to connect Telegram, or disconnect it |
| GET | `/api/categories` | list of categories |
| GET | `/api/stats` | per-person stats |
| GET | `/api/health` | health check for probes |

Error messages follow the `Accept-Language` header (English by default). Interactive documentation is available at `/docs`.

## Releases

Changes by version are in [CHANGELOG.md](CHANGELOG.md) and on the [Releases](https://github.com/gera-corp/to-do-board/releases) page. Before pushing a tag, add a `## [X.Y.Z]` section to the changelog: CI checks for it before building and publishes it as the release notes once the image and chart are out.

## Translations

Source strings are written in Russian and serve as keys. The English translations live in `app/static/i18n.js` (interface) and `app/i18n.py` (API errors, notifications, bot). Tests won't let a string ship without a translation.

## Tests

```bash
.venv/bin/pip install --require-hashes -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Dependencies are pinned with hashes in `requirements.txt`. To update them, edit `requirements.in` and rebuild the lock file with the command in its header.

## Container

GitHub Actions builds the image on every push to `main` and on `v*.*.*` tags and publishes it to `ghcr.io/gera-corp/to-do-board` for `linux/amd64` and `linux/arm64`.

| Tag | Published on |
|---|---|
| `latest`, `sha-<commit>` | push to `main` |
| `1.2.3`, `1.2` | tag `v1.2.3` |

How the image is hardened:
- distroless base (`gcr.io/distroless/python3-debian13:nonroot`): no shell, package manager or extra tools;
- runs as the unprivileged user `65532`;
- the code is owned by root and read-only; the app can write only to `/data`;
- works with a read-only root filesystem and no Linux capabilities;
- base images are pinned by digest, dependencies by hash, GitHub Actions by commit SHA;
- ships with an SBOM and a signed provenance attestation;
- Trivy scans every image, the report goes to the Security tab, and critical vulnerabilities with an available fix stop the build;
- Dependabot proposes updates to base images, dependencies and actions every week.

Run it locally with the same restrictions as in the cluster:

```bash
docker run --rm -p 8000:8000 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp -v to-do-data:/data \
  ghcr.io/gera-corp/to-do-board:latest
```

## Kubernetes

The Helm chart lives in `charts/to-do-board`. On a `v1.2.3` tag CI first builds and checks the `1.2.3` image and then publishes the chart of the same version to `oci://ghcr.io/gera-corp/charts/to-do-board`.

In the home cluster the board is managed by ArgoCD. The Application and the cluster values are in [gera-corp-org/argocd](https://github.com/gera-corp-org/argocd), under `home_cluster/helm_app/to-do-board`. ArgoCD tracks `0.*` versions, so a release is just a tag:

```bash
git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z
```

A few minutes after CI finishes, ArgoCD updates the board on its own.

Main chart values:

| Value | Default | Purpose |
|---|---|---|
| `ingress.host` | — | board domain (required) |
| `ingress.className` | `traefik` | IngressClass |
| `ingress.annotations` | cert-manager `letsencrypt-cloud-production` | Ingress annotations |
| `config.timezone` | `UTC` | time zone for due dates and the morning digest |
| `config.telegramDigestHour` | `9` | hour of the morning digest in Telegram and by email |
| `config.sqliteJournalMode` | `DELETE` | SQLite journal mode; `WAL` only on local disks, not NFS |
| `config.googleClientId` | empty | Google OAuth client ID; empty turns sign-in with Google off |
| `config.passwordLogin` | `true` | password sign-in |
| `config.ldap` | off | sign-in through LDAP or Active Directory; put `LDAP_BIND_PASSWORD` into `vault.env` |
| `config.smtp` | off | email notifications; put `SMTP_PASSWORD` into `vault.env`, `caSecret` is a Secret with `ca.pem` |
| `config.trustedProxies` | `*` | proxies trusted for the client address (safe while the NetworkPolicy admits only the ingress controller) |
| `vault.secretPath` | `secret/data/to-do-board` | secret in Vault (KV v2) |
| `vault.env` | `TELEGRAM_BOT_TOKEN` | which secret keys become environment variables (add `GOOGLE_CLIENT_SECRET` for Google) |
| `vault.role`, `vault.authPath`, `vault.addr` | empty | empty means the webhook's own settings |
| `persistence.size` | `1Gi` | database volume size |
| `networkPolicy.ingressNamespaces` | `traefik` | namespaces allowed to reach the board |

Secrets are injected by the bank-vaults `vault-secrets-webhook`: the manifest only holds a reference such as `vault:secret/data/to-do-board#TELEGRAM_BOT_TOKEN`, and the real value is placed into the process memory at start. It never lands in a Kubernetes Secret or in etcd. When the secret changes in Vault, the pod restarts (`vault-secrets-reloader`).

The database volume is protected from deletion: neither `helm uninstall` nor deleting the ArgoCD application touches it.

The pod runs under the `restricted` Pod Security profile: no root, no capabilities, `seccomp: RuntimeDefault` and a read-only root filesystem. There is always exactly one replica, updated with `Recreate`, because SQLite doesn't support multiple writers.
