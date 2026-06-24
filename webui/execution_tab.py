"""Execution tab UI for the Telegram Media Downloader Web UI."""

import logging
import os
import time
import urllib.parse

from nicegui import ui

import db
import media_downloader


def build_execution_tab(
    config: dict, load_config_fn, chat_inputs: list, open_media_fn, this_dir: str
):
    """Build the Execution tab panel contents.

    Parameters
    ----------
    config : dict
        Loaded configuration dictionary.
    load_config_fn : callable
        Function to reload config from disk.
    chat_inputs : list
        List of chat input dicts (from config tab) to update last_read after run.
    open_media_fn : callable
        ``open_media(url, filename)`` for the preview dialog.
    this_dir : str
        Absolute path to the project root directory.
    """
    # Shared state
    is_running = {"value": False}
    is_monitoring = {"value": False}
    active_downloads = {}
    account_premium = {"value": None}
    total_gb_label = None
    monitor_client_ref = {"client": None}
    stop_monitoring_fn = {"fn": None}

    # Speed meter state
    speed_byte_window = []
    last_known_bytes = {}
    speed_label = None

    def compute_speed_str():
        now = time.monotonic()
        recent = [(t, b) for t, b in speed_byte_window if now - t <= 3.0]
        duration = now - recent[0][0] if recent else 0
        if duration <= 0:
            return ""
        total_bytes = sum(b for _, b in recent)
        bps = total_bytes / duration
        if bps >= 1024**3:
            return f"{bps / 1024**3:.1f} GB/s"
        if bps >= 1024**2:
            return f"{bps / 1024**2:.1f} MB/s"
        if bps >= 1024:
            return f"{bps / 1024:.1f} KB/s"
        return f"{bps:.0f} B/s"

    def update_speed_display():
        if speed_label is not None:
            s = compute_speed_str()
            speed_label.set_text(f"\u2b07 {s}" if s else "\u2b07 \u2014")

    # Section Header + Status
    with ui.column().style("gap: 2px; margin-bottom: 28px;"):
        with ui.row().classes("items-center justify-between").style("width: 100%;"):
            with ui.column().style("gap: 2px;"):
                ui.label("Execution").classes("section-title")
                ui.label(
                    "Start downloading or monitoring media from your configured chats."
                ).classes("section-subtitle")
            with ui.row().style("gap: 8px; align-items: center;"):
                premium_badge = ui.html(
                    '<span class="status-badge status-free">\u2014 Account</span>'
                )
                status_label = ui.html(
                    '<span class="status-badge status-idle">'
                    '<span style="width:6px;height:6px;border-radius:50%;'
                    'background:currentColor;display:inline-block;"></span>'
                    " Idle</span>"
                )

    # Metrics row (speed + pending + total GB)
    with ui.row().style("gap: 16px; margin-bottom: 20px; align-items: center;"):
        speed_label = ui.label("\u2b07 \u2014").style(
            "font-size: 13px; font-weight: 500; color: var(--text-secondary);"
        )
        pending_label = ui.label("").style(
            "font-size: 13px; font-weight: 500; color: var(--text-tertiary);"
        )
        total_gb_label = ui.label("").style(
            "font-size: 13px; font-weight: 500; color: var(--text-tertiary);"
        )

    # Two-column layout
    with ui.row().style(
        "width: 100%; gap: 20px; align-items: flex-start; flex-wrap: wrap;"
    ):
        # Left column
        with ui.column().style("flex: 1; min-width: 300px; gap: 16px;"):
            # Active Downloads
            with ui.element("div").classes("premium-card").style("padding: 24px;"):
                with ui.row().classes("items-center").style(
                    "gap: 10px; margin-bottom: 16px;"
                ):
                    ui.icon("downloading", size="sm", color="primary")
                    ui.label("Active Downloads").style(
                        "font-size: 15px; font-weight: 600;"
                        " color: var(--text-primary);"
                    )

                progress_container = ui.column().style(
                    "width: 100%; gap: 8px; max-height: 320px;"
                    " overflow-y: auto; padding-right: 4px;"
                )
                ui.html(
                    '<div style="padding: 12px 0; text-align: center; '
                    'color: var(--text-tertiary); font-size: 13px;"'
                    ' id="empty-state">No active downloads</div>'
                )

            # Buttons
            with ui.row().style("gap: 8px; width: 100%;"):
                ui.button(
                    "Start History Download",
                    on_click=lambda: ui.timer(0.0, run_downloader, once=True),
                    icon="play_arrow",
                ).props('unelevated color="primary"').style(
                    "flex: 1; height: 48px; font-size: 14px; font-weight: 600;"
                )
                ui.button(
                    "Start Monitoring",
                    on_click=lambda: ui.timer(0.0, run_monitor, once=True),
                    icon="radar",
                ).props('unelevated color="info"').style(
                    "flex: 1; height: 48px; font-size: 14px; font-weight: 600;"
                )

            with ui.row().style("gap: 8px; width: 100%;"):
                stop_monitor_btn = (
                    ui.button(
                        "Stop Monitoring",
                        on_click=lambda: stop_monitoring_fn["fn"](),
                        icon="stop",
                    )
                    .props('outline color="negative"')
                    .style(
                        "flex: 1; height: 40px; font-size: 13px;"
                        " font-weight: 500; display: none;"
                    )
                )

        # Right column (Terminal always visible)
        with ui.column().style("flex: 0 0 400px; min-width: 320px;"):
            with ui.element("div").classes("premium-card").style(
                "padding: 0; overflow: hidden;"
            ):
                with ui.row().classes("items-center").style(
                    "gap: 10px; padding: 16px 16px 0 16px;"
                ):
                    ui.icon("terminal", size="sm", color="primary")
                    ui.label("Terminal Output").style(
                        "font-size: 15px; font-weight: 600;"
                        " color: var(--text-primary);"
                    )
                log_area = (
                    ui.log(max_lines=300)
                    .classes("terminal-log")
                    .style(
                        "width: 100%; height: 480px; min-height: 480px;"
                        " max-height: 480px; padding: 16px; font-size: 13px;"
                        " line-height: 1.7; overflow-y: auto;"
                    )
                )

    # Custom logging handler
    class UILogHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                log_area.push(msg)
            except Exception:
                pass

    ui_logger = UILogHandler()
    ui_logger.setFormatter(logging.Formatter("%(message)s"))

    def update_status(text, style_class):
        dot = (
            '<span style="width:6px;height:6px;border-radius:50%;'
            'background:currentColor;display:inline-block;"></span>'
        )
        status_label.content = (
            f'<span class="status-badge {style_class}">{dot} {text}</span>'
        )

    def update_premium_badge(premium):
        if premium is True:
            premium_badge.content = (
                '<span class="status-badge status-premium">\u2b50 Premium</span>'
            )
        elif premium is False:
            premium_badge.content = '<span class="status-badge status-free">Free</span>'
        else:
            premium_badge.content = (
                '<span class="status-badge status-free">\u2014 Account</span>'
            )

    def update_total_gb():
        if total_gb_label is not None:
            total_bytes = db.get_total_downloaded_bytes()
            total_gb_label.set_text(f"Total: {db.format_bytes(total_bytes)}")

    async def check_premium():
        if account_premium["value"] is None:
            fresh = load_config_fn()
            premium = await media_downloader.check_account_premium(fresh)
            account_premium["value"] = premium
            update_premium_badge(premium)

    def ui_progress_hook(desc, current, total, file_path=None, media_type=None):
        nonlocal speed_byte_window, last_known_bytes

        # Speed tracking
        now = time.monotonic()
        prev = last_known_bytes.get(desc, 0)
        if current >= prev:
            delta = current - prev
            if delta > 0:
                speed_byte_window.append((now, delta))
            last_known_bytes[desc] = current
            speed_byte_window = [(t, b) for t, b in speed_byte_window if now - t <= 5.0]
        if current >= total and desc in last_known_bytes:
            del last_known_bytes[desc]

        if desc not in active_downloads:
            with progress_container:
                row = (
                    ui.row()
                    .classes("dl-row")
                    .style("width: 100%; align-items: center; gap: 12px;")
                )
                with row:
                    name_label = ui.label(desc).style(
                        "font-size: 13px; font-weight: 500;"
                        " color: var(--text-secondary);"
                        " white-space: nowrap; overflow: hidden;"
                        " text-overflow: ellipsis; width: 35%;"
                    )
                    bar = (
                        ui.linear_progress(value=0, show_value=False)
                        .props("instant-feedback color=primary size=6px rounded")
                        .style("width: 30%; border-radius: 3px;")
                    )
                    pct_label = ui.label("0%").style(
                        "font-size: 12px; font-weight: 500;"
                        " color: var(--text-tertiary); text-align: center;"
                        " width: 20%; font-variant-numeric: tabular-nums;"
                    )
                    action_col = ui.column().style(
                        "width: 10%; min-width: 50px; align-items: flex-end;"
                    )
                active_downloads[desc] = (
                    row,
                    name_label,
                    bar,
                    pct_label,
                    action_col,
                )

        row, name_label, bar, pct_label, action_col = active_downloads[desc]
        if total > 0:
            fraction = current / total
            bar.set_value(fraction)
            pct_label.set_text(
                f"{current / 1024 / 1024:.1f}M / {total / 1024 / 1024:.1f}M "
                f"({fraction * 100:.1f}%)"
            )
            if current >= total:
                name_label.style(
                    "font-size: 13px; font-weight: 600; color: var(--positive);"
                    " white-space: nowrap; overflow: hidden;"
                    " text-overflow: ellipsis; width: 35%;"
                )
                if desc.startswith("Downloading "):
                    name_label.set_text(desc.replace("Downloading ", "\u2713 ", 1))

                if file_path and not getattr(row, "has_open_btn", False):
                    row.has_open_btn = True
                    global_download_dir = config.get("download_directory", "")
                    file_url = ""
                    try:
                        abs_fpath = os.path.abspath(file_path)
                        abs_base = (
                            os.path.abspath(global_download_dir)
                            if global_download_dir
                            else this_dir
                        )
                        if abs_fpath.startswith(abs_base):
                            rel_path = os.path.relpath(abs_fpath, abs_base)
                            rel_path = rel_path.replace("\\", "/")
                            encoded_path = urllib.parse.quote(rel_path, safe="/")
                            file_url = f"/media/{encoded_path}"
                    except Exception:
                        pass
                    if file_url:
                        with action_col:
                            fname = os.path.basename(file_path)
                            ui.button(
                                "Open",
                                on_click=lambda u=file_url, n=fname: open_media_fn(
                                    u, n
                                ),
                            ).props('flat dense color="primary" size="sm"').style(
                                "font-size: 12px;"
                            )
        else:
            bar.set_value(0)
            pct_label.set_text(f"{current} bytes")

    async def run_downloader():
        if is_running["value"]:
            ui.notify("Downloader is already running!", type="warning")
            return
        is_running["value"] = True
        main_logger = logging.getLogger("media_downloader")
        main_logger.addHandler(ui_logger)
        try:
            log_area.clear()
            progress_container.clear()
            active_downloads.clear()
            speed_byte_window.clear()
            last_known_bytes.clear()
            update_status("Running", "status-running")
            ui.notify("Initializing Telegram Client...", type="info")
            media_downloader.UI_PROGRESS_HOOK = ui_progress_hook
            fresh_config = load_config_fn()
            updated_config = await media_downloader.begin_import(
                fresh_config, pagination_limit=100
            )
            media_downloader.update_config(updated_config)
            updated_chats = updated_config.get("chats", [])
            for i, c in enumerate(updated_chats):
                if i < len(chat_inputs):
                    chat_inputs[i]["last_read"].value = c.get("last_read_message_id", 0)
            total_failures = sum(
                len(set(flist)) for flist in media_downloader.FAILED_IDS.values()
            )
            if total_failures > 0:
                update_status(f"Done \u00b7 {total_failures} errors", "status-warning")
                log_area.push(
                    f"Warning: {total_failures} files failed. "
                    "Check config.yaml ids_to_retry."
                )
                ui.notify(
                    f"Finished, but {total_failures} files failed.",
                    type="warning",
                    position="top",
                )
            else:
                update_status("Complete", "status-success")
                ui.notify(
                    "Download complete!",
                    type="positive",
                    position="top",
                )
        except Exception as e:
            update_status("Error", "status-error")
            log_area.push(f"Error: {str(e)}")
            ui.notify(f"Error: {str(e)}", type="negative", position="top")
        finally:
            media_downloader.UI_PROGRESS_HOOK = None
            is_running["value"] = False
            main_logger.removeHandler(ui_logger)
            update_total_gb()

    async def run_monitor():
        if is_monitoring["value"]:
            ui.notify("Monitor is already running!", type="warning")
            return
        is_monitoring["value"] = True
        main_logger = logging.getLogger("media_downloader")
        main_logger.addHandler(ui_logger)
        try:
            log_area.clear()
            progress_container.clear()
            active_downloads.clear()
            speed_byte_window.clear()
            last_known_bytes.clear()
            update_status("Monitoring", "status-monitoring")
            ui.notify("Starting monitor mode...", type="info")
            media_downloader.UI_PROGRESS_HOOK = ui_progress_hook
            fresh_config = load_config_fn()
            client = await media_downloader.begin_monitor(fresh_config)
            monitor_client_ref["client"] = client
            stop_monitor_btn.style("display: block;")
            log_area.push("Monitor active. Listening for new media...")
            ui.notify(
                "Monitor mode active. Listening for new messages...",
                type="positive",
            )
        except Exception as e:
            is_monitoring["value"] = False
            update_status("Error", "status-error")
            log_area.push(f"Error: {str(e)}")
            ui.notify(f"Error: {str(e)}", type="negative", position="top")
            main_logger.removeHandler(ui_logger)

    async def stop_monitoring():
        if monitor_client_ref["client"] is not None:
            try:
                await monitor_client_ref["client"].disconnect()
            except Exception:
                pass
            monitor_client_ref["client"] = None
        is_monitoring["value"] = False
        stop_monitor_btn.style("display: none;")
        update_status("Idle", "status-idle")
        media_downloader.UI_PROGRESS_HOOK = None
        main_logger = logging.getLogger("media_downloader")
        try:
            main_logger.removeHandler(ui_logger)
        except Exception:
            pass
        ui.notify("Monitor stopped.", type="info")

    stop_monitoring_fn["fn"] = stop_monitoring

    # Timers
    ui.timer(0.5, update_speed_display)

    def update_pending():
        if pending_label is not None:
            total = sum(media_downloader.PENDING_IDS.values())
            if total > 0:
                pending_label.set_text(f"\U0001f4e5 {total} pending")
            else:
                pending_label.set_text("")

    ui.timer(0.5, update_pending)
    ui.timer(1.0, update_total_gb)

    # Check premium status on load
    ui.timer(0.3, check_premium, once=True)
    update_total_gb()
