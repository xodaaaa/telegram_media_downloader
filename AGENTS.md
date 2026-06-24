# Telegram Media Downloader - Agent Context

This file provides architectural context, coding guidelines, and project
conventions for AI coding agents (Gemini, Copilot, Cursor, etc.) working on
this repository.

## Project Overview

A Python-based utility script using [Telethon](https://docs.telethon.dev/) to
download media files (audio, document, photo, video, voice, video_note) from
Telegram chats and channels without re-downloading files that already exist in
the target directory. Includes a full **NiceGUI Web UI** for interactive
configuration, execution monitoring, download history browsing, and media
preview.

## Core Architecture

- **Entry Points**:
  - `media_downloader.py` — CLI entry point. Handles three execution modes.
  - `webui.py` — Web UI entry point (requires Python >= 3.10 + NiceGUI).
    Serves a 4-tab SPA at `http://localhost:8080`.
- **Configuration**: Managed via `config.yaml` (see `config.yaml.example` for
  the schema). Global settings act as fallbacks for per-chat overrides inside
  the `chats:` list.
- **State Management**: Global in-memory dictionaries in `media_downloader.py`:
  - `FAILED_IDS` / `DOWNLOADED_IDS` — per-chat lists of message IDs (success/fail).
  - `PROCESSED_IDS` / `CURRENT_BATCH_IDS` — transient batch tracking for safe resumption.
  - `PENDING_IDS` — number of files currently downloading per chat (used by UI).
  - `BACKLOG_ITERATED` / `BACKLOG_DONE` — backlog counters for history mode UI.
  - `UI_PROGRESS_HOOK` — callback set by Web UI to receive real-time progress.
- **Persistence**: `update_config()` saves `FAILED_IDS` into `ids_to_retry` and
  writes the whole `config.yaml` after every batch (or on stop). Uses set
  operations to deduplicate and sort `ids_to_retry`.
- **Concurrency**:
  - **Chats**: sequential by default; parallel via `asyncio.gather` if
    `parallel_chats: true`.
  - **Downloads**: `asyncio.Semaphore(max_concurrent_downloads)` limits
    simultaneous file streams per batch.

## Execution Modes

The `mode` key in `config.yaml` controls behavior:

| Mode | Behavior |
|---|---|
| `history` | Downloads backlog from the past and exits (default, backward compatible). |
| `monitor` | Skips backlog; listens for `events.NewMessage` in real time forever. |
| `history_monitor` | Runs full backlog (`begin_import`), then auto-switches to monitor mode. |

Key functions implementing these modes:

- `begin_import(config, pagination_limit, client_ref=None)` — history backlog.
  Exposes `client_ref` so the Web UI can disconnect it to stop early.
- `begin_monitor(config)` — creates client, registers `NewMessage` handlers
  per chat via `register_monitor_handler()`. Returns the connected client
  (non-blocking) so callers can decide how to wait.
- `register_monitor_handler(client, global_config, chat_conf)` — resolves
  per-chat settings via `_resolve_monitor_settings()` (same global→local
  fallback pattern as `process_chat`), creates a semaphore, and registers
  the event handler.
- `check_account_premium(config)` — connects briefly, calls `client.get_me()`,
  and returns `{premium, first_name, last_name, username}`. Used by the Web UI
  badge.
- `send_auth_code(api_id, api_hash, phone)` — creates a client, connects,
  requests an SMS code, and returns `{phone_code_hash, client}`. Used by the
  setup wizard for first-run phone verification.
- `verify_auth_code(client, phone, code, phone_code_hash)` — verifies the SMS
  code, calls `client.sign_in()`, creates the `.session` file, and disconnects.
  Returns `True` on success.
- `_resolve_download_delay(download_delay)` — parses and computes a download
  delay value from config (fixed float, random range `[min, max]`, or `None`).
  Centralized helper used by both `process_messages` (history) and
  `register_monitor_handler` (monitor) to avoid code duplication.

### `history_monitor` auto-switch behavior

**CLI** (`main()` in `media_downloader.py`):
1. Runs `begin_import()` for the full backlog.
2. On success: saves config via `update_config()`, logs "Backlog complete.
   Switching to Monitor mode...", calls `begin_monitor()`, then blocks via
   `client.run_until_disconnected()`.
3. On `KeyboardInterrupt` during backlog: skips monitor phase entirely,
   saves config, and returns.

**Web UI** (`run_downloader()` in `execution_tab.py`):
1. Runs `begin_import()` via `run_downloader()`.
2. On success, checks `load_config_fn().get("mode") == "history_monitor"`.
3. If true: hides "Stop Download" button, sets `is_running = False`, calls
   `run_monitor()` which registers handlers and shows "Stop Monitoring".
4. The `switched_to_monitor` flag prevents the `finally` block from
   removing the log handler or UI_PROGRESS_HOOK (needed by the monitor).
5. Only "Stop Monitoring" remains visible after the switch.

## Web UI Architecture (`webui.py` + `webui/`)

The UI is a single-page app using NiceGUI's declarative layout:

```
┌──────────────────────────────────────────────────────┐
│ Sidebar (260px)  │  Tab Content (flex:1)             │
│                    ┌─────────────────────────────────┐│
│ TG Downloader      │ [Configuration|Execution|History││
│                    │  |Terminal]                     ││
│ ≡ WORKSPACE        │                                 ││
│  □ Configuration   │  Tab panel content here         ││
│  □ Execution       │                                 ││
│  □ History         │                          [Badge]││
│  □ Terminal        │                                 ││
│                    └─────────────────────────────────┘│
│ ≡ Take Tour                                          │
│ ≡ Dark Mode toggle                                   │
└──────────────────────────────────────────────────────┘
```

### Tab: Configuration (`webui/config_tab.py`)
- API Credentials card (api_id, api_hash with show/hide toggle).
- Download Settings: directory, date filters, max messages, concurrency,
  delay, media types, file formats, parallel_chats.
- Target Chats card with per-chat overrides (directory, types, formats, etc.).
- Save/Load from disk actions. Returns `(global_inputs, chat_inputs)` for
  other tabs to reference.

### Tab: Execution (`webui/execution_tab.py`)
- **Account badge**: top-right, auto-detects Premium/Free via
  `check_account_premium()`. Shows name and ⭐ for Premium. Runs once on page
  load via `ui.timer(0.5, ..., once=True)`.
- **Status badge**: centered below section title. States:
  `status-idle` (grey), `status-running` (blue), `status-monitoring` (purple
  with pulse animation), `status-success` (green), `status-error` (red).
- **Live metric cards** (centered row, value on top, label below):
  - `⬇ SPEED` — global aggregated download speed (sliding 5s window of byte
    deltas from `UI_PROGRESS_HOOK`). Refreshes every 0.5s. Shows "0 B/s" when idle.
  - `📥 ACTIVE / QUEUED` — format `X / Y`. X = `sum(PENDING_IDS.values())`
    (files currently downloading). Y = `max(0, BACKLOG_ITERATED - BACKLOG_DONE)`
    (backlog remaining). Refreshes every 0.5s. Shows "0 / 0" when idle.
  - `📦 TOTAL` — `db.get_total_downloaded_bytes()` formatted via
    `db.format_bytes()`. Refreshes every 1s.
- **Active Downloads card**: max 4 visible rows. Active files appear first via
  CSS `order: 0`, completed files pushed down via `order: 1`. Each entry is a
  mutable **list** (not tuple) with indices: `[0]` row, `[1]` name_label,
  `[2]` bar, `[3]` info_label, `[4]` action_col, `[5]` start_time,
  `[6]` last_bytes, `[7]` visible (bool), `[8]` completed (bool).
  - When >4 entries exist, the oldest **completed** entry is hidden first
    (`display: none`). If all are active, the oldest active is hidden.
  - Each row shows: filename (truncated), progress bar, percentage + ETA
    (e.g. "73% · 2m left"). ETA computed from per-file elapsed time and
    bytes transferred.
  - Completed rows show ✓ green filename + "Done" + "Open" button.
  - Empty state: "No active downloads" label shown/hidden via
    `_update_empty_state()` which checks `any(entry[7] for entry in active_downloads.values())`.
    **Important**: this function was bugged due to duplicate definitions
    (the old `_hide_empty_state` and `_show_empty_state` were renamed but
    not deleted, causing the second definition to override the first).
- **Buttons** (with descriptive subtitles below):
  - **Start History Download** — subtitle "Downloads backlog from the past".
    Calls `run_downloader()` → `begin_import()` → saves on completion/stop.
  - **Start Monitoring** — subtitle "Listens for new incoming media".
    Calls `run_monitor()` → `begin_monitor()` → stores client ref for stop.
  - **Stop Download** — appears during history download. Calls `stop_download()`
    which disconnects `download_client_ref["client"]`. Progress is saved in
    the `finally` block via `media_downloader.update_config(fresh)`.
  - **Stop Monitoring** — appears during monitoring. Calls `stop_monitoring()`
    which disconnects `monitor_client_ref["client"]` and cleans up log handler.
- **Mutual exclusion**: history blocks monitor, monitor blocks history.
  `run_downloader` checks `is_monitoring["value"]` and vice versa. Duplicate
  clicks on the same mode show a warning toast ("already running").
- **State cleanup on mode start**: ALL global dicts (`PENDING_IDS`,
  `FAILED_IDS`, `DOWNLOADED_IDS`, `PROCESSED_IDS`, `CURRENT_BATCH_IDS`,
  `BACKLOG_ITERATED`, `BACKLOG_DONE`) are `.clear()`ed when starting
  history or monitor to prevent cross-mode data leaks.
- **NiceGUI caveat**: Button references (DOM elements) cannot be captured in
  closures because NiceGUI's auto-reload (WatchFiles) recreates the entire
  UI tree on file changes, invalidating old element references. Mutable
  containers (`dict`) like `stop_monitoring_fn` and `stop_download_fn` are
  used to store callback references that survive reloads.

### Tab: History (`webui/history_tab.py`)
- Search, type filter, column sorting, pagination (20 items/page).
- **Total downloaded** label at top, auto-refreshes every 2s.
- "Open" preview via modal dialog (supports images, videos, audio, PDFs).
- "Clear All" resets the history DB.

### Tab: Terminal (`webui.py` inline)
- 4th sidebar nav item ("Terminal" with `terminal` icon), rendered inside a
  `ui.tab_panel`.
- `ui.log` widget (500 lines max) receives real-time log output from both
  history and monitor modes via a `UILogHandler` attached to the
  `media_downloader` logger.
- `log_area_holder` dict pattern: `log_area_holder["widget"]` is set in
  the Terminal tab's `tab_panel` and passed to `build_execution_tab()`.
  The `UILogHandler.emit()` accesses `log_area.get("widget")` to push messages.
  This pattern exists because NiceGUI renders columns left-to-right, so
  `log_area` must be defined before it's used — the holder provides indirection.
- Section header centered like other tabs.

### Tour (`webui/tour.py`)
- 11-step interactive walkthrough covering all features.
- First-visit detection via `localStorage`; auto-shows for new users.
- Steps: Welcome → API Credentials → Download Directory → Concurrency &
  Pacing → Media Types → Target Chats → Save Config → Execution Modes →
  Running Downloads → Live Metrics → Terminal Tab → Download History.
- Navigates to the relevant sidebar tab (`config`, `execution`, `history`,
  `terminal`) for each step.

### Styles (`webui/styles.py`)
- CSS design system with light/dark mode tokens (`--surface`, `--accent`,
  `--text-primary`, etc.).
- `.premium-card`, `.section-title` (centered), `.section-subtitle` (centered),
  `.sidebar`, `.nav-item`, `.terminal-log`, `.dl-row`, `.status-badge`, etc.
- Status variants: `status-idle`, `status-running`, `status-monitoring`
  (with `@keyframes pulse-monitor`), `status-success`, `status-error`,
  `status-warning`, `status-premium`, `status-free`.
- Telethon noise suppression: `logging.getLogger("telethon").setLevel(WARNING)`,
  `logging.getLogger("asyncio").setLevel(CRITICAL)`, plus `sys.unraisablehook`
  override to silence `GeneratorExit` exceptions from Python 3.13.

### Setup Wizard (`webui/setup_wizard.py`)
- **Purpose**: guides first-time users through 3-step authentication setup
  without requiring manual `config.yaml` editing.
- **State detection** (in `webui.py`):
  - No API creds → full wizard (Steps 1→2→3)
  - No `.session` file → phone re-auth only (Step 2)
  - No `chat_id` → chat input only (Step 3)
  - All present → normal UI, no wizard
- **Step 1 — API Credentials**: `api_id` (number) + `api_hash` (text with 👁
  toggle). Validates both are non-empty. Link to my.telegram.org for new users.
- **Step 2 — Phone Verification**: `phone` input (international format) → "Send
  Code" button calls `media_downloader.send_auth_code()` → shows spinner →
  `code` input → "Verify" calls `media_downloader.verify_auth_code()`.
  On success: saves `phone` to config, advances to Step 3.
- **Step 3 — Target Chat**: `chat_id`/`@username` input. Options to "Skip" or
  "Finish". On Finish: saves `chat_id`, `chats` list, defaults for
  `download_delay: [15, 30]`, `max_concurrent_downloads: 4`,
  `media_types`, and `file_formats`.
- **Style constants**: `_PROPS_DENSE`, `_GAP_8`, `_COLOR_NEG`, `_COLOR_POS`,
  `_COLOR_SEC`, `_FLAT_GREY`, `_FONT_13`, `_TEXT_SUBTITLE` — extracted to
  module level (SonarCloud S1192 fix).
- **Helpers**: `_step_color(active, past)`, `_set_result(text, style)` — reduce
  cognitive complexity (SonarCloud S3776/S3358 fix).

## Database (`db.py`)

SQLite database at `downloads.sqlite3` (relative to `db.py`).

- **Table**: `download_history` (id, chat_id, message_id, file_name,
  file_size, download_timestamp, file_path, media_type).
- **Migration**: auto-adds `file_path` and `media_type` columns if missing.
- Key functions:
  - `record_download()` — called from `download_media` on success.
  - `get_recent_downloads()` — with search, filter, sort, pagination.
  - `get_total_downloaded_bytes()` — `SELECT COALESCE(SUM(file_size), 0)`;
    returns `int`. Used for the "Total GB" metric.
  - `format_bytes(n)` — human-readable (B → KB → MB → GB → TB). Handles
    `n <= 0` as "0 B".
  - `reset_history()` — clears all records.

## File Management (`utils/file_management.py`)

- `_file_md5()` — computes **SHA-256** hash (changed from MD5 for security
  linter compliance). Reads in 8KB chunks, properly using `with open(...)`.
  Function name kept as `_file_md5` for backward compatibility with tests.
- `get_next_name()` — generates `-copyN` suffixes to avoid overwrites.
- `manage_duplicate_file()` — compares SHA-256 hashes of files matching
  `-copy*` pattern; deletes the newer file if identical to an older copy.

## Config & Data Integrity

### `ids_to_retry` deduplication
`update_config()` uses Python `set` operations (`union`, `difference`) to
merge `FAILED_IDS` into `ids_to_retry` without duplicates. Results are
`sorted()` for readable config files.

### Deleted message handling
When `client.get_messages(chat_id, ids=ids_to_retry)` returns `None` entries
(for messages deleted from Telegram), `process_chat` skips them with
`if message is None: continue`. Additionally, `chat_conf["ids_to_retry"]`
is updated to remove orphan IDs: `[m.id for m in skipped_messages if m is not None]`.

### `download_delay` default
Changed from `null` to `[15, 30]` in `config.yaml.example` to provide
safer rate-limiting out of the box.

## Key Principles & Conventions

1. **Async & Telethon**: All core logic is `async`/`await`. Use native
   `asyncio` patterns.
2. **Rate Limiting**: `max_concurrent_downloads` (default 4) + `download_delay`
   (default `[15, 30]` — random 15–30s between files).
3. **Graceful Exits**: KeyboardInterrupt → stops fetching, calculates
   safe `last_read_message_id`, flushes config. Web UI Stop buttons
   disconnect the client gracefully and call `update_config()` in `finally`.
4. **Memory Safety**: `PROCESSED_IDS` and `CURRENT_BATCH_IDS` are purged
   between batches. `active_downloads` dict is cleared on mode start.
5. **Cross-mode Isolation**: All global state dicts are `.clear()`ed when
   starting a new mode to prevent leaks between history and monitor runs.
6. **No Duplicate Counting**: `PENDING_IDS` and `BACKLOG_DONE` are only
   incremented in the callers (`_download_with_limit` → `process_messages`
   for history mode, `_handler` → `register_monitor_handler` for monitor
   mode) — **never** in `download_media` itself. This prevents double-counting
   when both the caller and callee increment the same counter.
7. **Security**: SHA-256 for file deduplication. `config.yaml` and
   `*.session` files are in `.gitignore`. `api_hash` is masked by default
   in the Web UI.
8. **NiceGUI Closures**: Never capture DOM element references in closures
   that survive page reloads. Use mutable containers (`dict["fn"]`) for
   callback references instead.

## Development & Testing

- **Formatting & Linting**: `black`, `isort --profile black`, `mypy`
  (over `utils/` and `media_downloader.py`), `pylint` (with `.pylintrc`,
  max line 90, NumPy docstrings).
- **Testing**: `pytest tests/ -v`. 116 tests total across:
  - `test_media_downloader.py` — core download logic (41 tests).
  - `test_monitor.py` — monitor mode, handler registration, main dispatching (21 tests).
  - `test_db.py` — SQLite operations, format_bytes, total bytes (15 tests).
  - `test_webui.py` — Web UI tab rendering, styles, tour (21 tests).
  - `test_pacing.py` — download delay and concurrency (4 tests).
  - `test_config_manager.py` — YAML load/save (8 tests).
  - `utils/` tests — file management, logging, meta, updates.
- **Test isolation**: `tests/conftest.py` wraps `db.DB_PATH` in a
  `tempfile.TemporaryDirectory()` with `ignore_cleanup_errors=True` to
  prevent test runs from touching the real database and to avoid
  Windows SQLite file-lock issues during cleanup. Same pattern used in
  `test_db.py` (2 classes) and `test_config_manager.py`.
  `test_monitor.py` classes create fresh `asyncio` event loops in `setUp()`
  to avoid conflicts with `test_media_downloader.py`'s `tearDownClass` loop
  closure on Python 3.13.
- App version in `utils/__init__.py`.

## CI/CD (`.github/workflows/`)

### `unittest.yml` — Test Suite
- **Trigger**: push + pull_request on `master`.
- **Permissions**: `contents: read` (minimum privilege).
- **Jobs**: `test` (18 OS×Python matrix) + `coverage` (separate, after tests).
- **Strategy**: `fail-fast: false` — one OS failure doesn't cancel others.
- **Fork safety**: coverage job conditionally runs only for the main repo
  (`github.event.pull_request.head.repo.full_name == github.repository`),
  preventing Codecov token exposure on forks.
- **SHA pinning**: all actions pinned to full commit SHAs
  (`checkout@11bd...9af683`, `setup-python@0b93...90a2b`,
  `codecov-action@b9fd...00238`) per SonarCloud S7637.

### `code-checks.yml` — Linting
- **Trigger**: push + pull_request on `master` + `workflow_dispatch` (manual).
- **Steps**: checkout → setup-python 3.12 → `make dev_install` → `pre-commit
  run --all-files --show-diff-on-failure`.
- Uses `pip install pre-commit` directly instead of `pre-commit/action`.

## Dependencies

- **Runtime**: `telethon`, `pyyaml`, `tqdm`, `rich`, `cryptg`.
- **Web UI** (optional, Python >= 3.10): `nicegui` (installed via
  `requirements-webui.txt` or `make install_webui`).
- **Development**: `pytest`, `black`, `isort`, `mypy`, `pylint`,
  `pre-commit`. See `dev-requirements.txt` and `Makefile`.
