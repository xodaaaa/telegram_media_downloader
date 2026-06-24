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
  `check_account_premium()`. Shows name and ⭐ for Premium.
- **Status badge**: Idle / Running / Monitoring / Complete / Error with colored dot.
- **Live metric cards** (centered, labeled below each value):
  - `⬇ SPEED` — global aggregated download speed (sliding 3s window of byte deltas).
  - `📥 ACTIVE / QUEUED` — files currently downloading (`PENDING_IDS`) vs
    total backlog remaining (`BACKLOG_ITERATED - BACKLOG_DONE`).
  - `📦 TOTAL` — cumulative GB downloaded (`db.get_total_downloaded_bytes()`).
- **Active Downloads card**: max 4 rows without scroll, active files first
  (`order: 0`), completed files pushed down (`order: 1`). Each row shows:
  filename, progress bar, percentage + ETA (e.g. "73% · 2m left"). Completed
  rows show ✓ and an "Open" button to preview the file.
- **Buttons**:
  - **Start History Download** — runs `begin_import`, shows progress, saves
    on completion/stop.
  - **Start Monitoring** — runs `begin_monitor`, keeps the client alive for
    real-time downloads. Registers log handler.
  - **Stop Download** — appears while history is running. Disconnects client
    gracefully; `finally` block calls `update_config()` to save progress.
  - **Stop Monitoring** — disconnects monitor client and cleans up.
- **Mutual exclusion**: history blocks monitor, monitor blocks history.
  Duplicate clicks show a warning toast.
- **State cleanup**: all global dicts (`PENDING_IDS`, `FAILED_IDS`,
  `DOWNLOADED_IDS`, `PROCESSED_IDS`, `CURRENT_BATCH_IDS`, `BACKLOG_*`)
  are cleared when starting any mode to prevent cross-mode leaks.

### Tab: History (`webui/history_tab.py`)
- Search, type filter, column sorting, pagination (20 items/page).
- **Total downloaded** label at top, auto-refreshes every 2s.
- "Open ↗" preview via modal dialog (supports images, videos, audio, PDFs).
- "Clear All" resets the history DB.

### Tab: Terminal (`webui.py` inline)
- 4th sidebar nav item, rendered inside a `ui.tab_panel`.
- `ui.log` widget (500 lines max) receives real-time log output from both
  history and monitor modes via a `UILogHandler` attached to the
  `media_downloader` logger.
- `log_area_holder` dict pattern passes the `ui.log` reference to the
  Execution tab so it can push logs from async callbacks.

### Tour (`webui/tour.py`)
- 11-step interactive walkthrough covering all features.
- First-visit detection via `localStorage`; auto-shows for new users.
- Steps: Welcome → API Credentials → Download Directory → Concurrency &
  Pacing → Media Types → Target Chats → Save Config → Execution Modes →
  Running Downloads → Live Metrics → Terminal Tab → Download History.

### Styles (`webui/styles.py`)
- CSS design system with light/dark mode tokens.
- `.premium-card`, `.section-title`, `.section-subtitle`, `.sidebar`,
  `.nav-item`, `.terminal-log`, `.dl-row`, `.status-badge`, etc.
- Status variants: `status-idle`, `status-running`, `status-monitoring`,
  `status-success`, `status-error`, `status-warning`, `status-premium`,
  `status-free`.
- Pulse animation for monitoring status: `@keyframes pulse-monitor`.

## Database (`db.py`)

SQLite database at `downloads.sqlite3` (relative to `db.py`).

- **Table**: `download_history` (id, chat_id, message_id, file_name,
  file_size, download_timestamp, file_path, media_type).
- **Migration**: auto-adds `file_path` and `media_type` columns if missing.
- Key functions:
  - `record_download()` — called from `download_media` on success.
  - `get_recent_downloads()` — with search, filter, sort, pagination.
  - `get_total_downloaded_bytes()` — SUM(file_size) for the "Total GB" metric.
  - `format_bytes(n)` — human-readable (B → KB → MB → GB → TB).
  - `reset_history()` — clears all records.

## File Management (`utils/file_management.py`)

- `_file_md5()` — computes **SHA-256** hash (previously MD5; changed for
  security linter compliance) in 8KB chunks, properly using `with open(...)`.
- `get_next_name()` — generates `-copyN` suffixes to avoid overwrites.
- `manage_duplicate_file()` — compares hashes of files matching `-copy*`
  pattern; deletes the newer file if identical to an older copy.

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
   incremented in the callers (`_download_with_limit` for history mode,
   `_handler` for monitor mode) — never in `download_media` itself.
7. **Security**: SHA-256 for file deduplication. `config.yaml` and
   `*.session` files are in `.gitignore`. `api_hash` is masked by default
   in the Web UI.

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
- App version in `utils/__init__.py`.

## Dependencies

- **Runtime**: `telethon`, `pyyaml`, `tqdm`, `rich`, `cryptg`.
- **Web UI** (optional, Python >= 3.10): `nicegui` (installed via
  `requirements-webui.txt` or `make install_webui`).
- **Development**: `pytest`, `black`, `isort`, `mypy`, `pylint`,
  `pre-commit`. See `dev-requirements.txt` and `Makefile`.
