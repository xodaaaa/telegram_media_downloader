"""Execution tab UI for the Telegram Media Downloader Web UI."""

import logging
import os
import time
import urllib.parse

from nicegui import ui

import db
import media_downloader


def build_execution_tab(
    config, load_config_fn, chat_inputs, open_media_fn, this_dir, log_area
):
    """Build the Execution tab panel contents.

    Parameters
    ----------
    config : dict
        Loaded configuration dictionary.
    load_config_fn : callable
        Function to reload config from disk.
    chat_inputs : list
        List of chat input dicts (from config tab).
    open_media_fn : callable
        ``open_media(url, filename)`` for the preview dialog.
    this_dir : str
        Absolute path to the project root directory.
    log_area : dict
        Holder dict with key ``"widget"`` pointing to the shared
        ``ui.log`` terminal widget.
    """

    def _log_widget():
        return log_area.get("widget") if isinstance(log_area, dict) else log_area

    # Shared state
    is_running = {"value": False}
    is_monitoring = {"value": False}
    active_downloads = {}
    total_gb_label = None
    monitor_client_ref = {"client": None}
    download_client_ref = {"client": None}
    stop_monitoring_fn = {"fn": None}
    stop_download_fn = {"fn": None}
    empty_state_ref = {}

    # Speed meter state (global)
    speed_byte_window = []
    last_known_bytes = {}
    speed_label = None
    download_order = []  # ordered list of desc keys (active first)

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
            speed_label.set_text(s if s else "0 B/s")

    # Section Header + Status
    with ui.column().style("gap: 2px; margin-bottom: 28px; align-items: center;"):
        ui.label("Execution").classes("section-title")
        ui.label(
            "Download or monitor media from your configured chats."
        ).classes("section-subtitle")
        status_label = ui.html(
            '<span class="status-badge status-idle">'
            '<span style="width:6px;height:6px;border-radius:50%;'
            'background:currentColor;display:inline-block;"></span>'
            " Idle</span>"
        )

    def update_status(text, style_class):
        dot = (
            '<span style="width:6px;height:6px;border-radius:50%;'
            'background:currentColor;display:inline-block;"></span>'
        )
        status_label.content = (
            f'<span class="status-badge {style_class}">{dot} {text}</span>'
        )

    def update_total_gb():
        if total_gb_label is not None:
            total_bytes = db.get_total_downloaded_bytes()
            total_gb_label.set_text(db.format_bytes(total_bytes))

    # Metrics row
    with ui.row().style(
        "gap: 32px; margin-bottom: 20px; align-items: end; justify-content: center;"
    ):
        with ui.column().style("gap: 2px; align-items: center;"):
            speed_label = ui.label("\u2014").style(
                "font-size: 18px; font-weight: 700;"
                " color: var(--accent); font-variant-numeric: tabular-nums;"
            )
            ui.label("\u2b07 speed").style(
                "font-size: 10px; font-weight: 500;"
                " color: var(--text-tertiary); text-transform: uppercase;"
                " letter-spacing: 0.05em;"
            )
        with ui.column().style("gap: 2px; align-items: center;"):
            pending_label = ui.label("0 / 0").style(
                "font-size: 18px; font-weight: 700;"
                " color: var(--text-secondary); font-variant-numeric: tabular-nums;"
            )
            ui.label("\U0001f4e5 active / queued").style(
                "font-size: 10px; font-weight: 500;"
                " color: var(--text-tertiary); text-transform: uppercase;"
                " letter-spacing: 0.05em;"
            )
        with ui.column().style("gap: 2px; align-items: center;"):
            total_gb_label = ui.label("\u2014").style(
                "font-size: 18px; font-weight: 700;"
                " color: var(--text-primary); font-variant-numeric: tabular-nums;"
            )
            ui.label("\U0001f4e6 total").style(
                "font-size: 10px; font-weight: 500;"
                " color: var(--text-tertiary); text-transform: uppercase;"
                " letter-spacing: 0.05em;"
            )

    # Active Downloads card
    with ui.element("div").classes("premium-card").style(
        "padding: 24px; margin-bottom: 16px;"
    ):
        with ui.row().classes("items-center").style("gap: 10px; margin-bottom: 16px;"):
            ui.icon("downloading", size="sm", color="primary")
            ui.label("Active Downloads").style(
                "font-size: 15px; font-weight: 600;" " color: var(--text-primary);"
            )

        progress_container = ui.column().style(
            "width: 100%; gap: 8px; padding-right: 4px;"
        )
        empty_state_ref["el"] = ui.label("No active downloads").style(
            "padding: 16px 0; text-align: center;"
            " color: var(--text-tertiary); font-size: 13px;"
            " width: 100%;"
        )

    def _update_empty_state():
        if "el" in empty_state_ref:
            has_visible = any(
                entry[7] for entry in active_downloads.values()
            )
            if has_visible:
                empty_state_ref["el"].style("display: none;")
            else:
                empty_state_ref["el"].style("")

    # Buttons
    with ui.row().style("gap: 8px; width: 100%; margin-bottom: 4px;"):
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
    with ui.row().style("gap: 8px; width: 100%; margin-bottom: 8px;"):
        ui.label("Downloads backlog from the past").style(
            "flex: 1; font-size: 10px; color: var(--text-tertiary);"
            " text-align: center;"
        )
        ui.label("Listens for new incoming media").style(
            "flex: 1; font-size: 10px; color: var(--text-tertiary);"
            " text-align: center;"
        )

    with ui.row().style("gap: 8px; width: 100%; margin-bottom: 8px;"):
        stop_dl_btn = (
            ui.button(
                "Stop Download",
                on_click=lambda: stop_download_fn["fn"](),
                icon="stop",
            )
            .props('outline color="negative"')
            .style(
                "flex: 1; height: 40px; font-size: 13px;"
                " font-weight: 500; display: none;"
            )
        )

    with ui.row().style("gap: 8px; width: 100%;"):
        stop_btn = (
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

    # Custom logging handler
    class UILogHandler(logging.Handler):
        def emit(self, record):
            try:
                msg = self.format(record)
                widget = (
                    log_area.get("widget") if isinstance(log_area, dict) else log_area
                )
                if widget is not None:
                    widget.push(msg)
            except Exception:
                pass

    ui_logger = UILogHandler()
    ui_logger.setFormatter(logging.Formatter("%(message)s"))

    def _update_empty_state():
        if "el" in empty_state_ref:
            empty_state_ref["el"].style("display: none;")

    def _update_empty_state():
        if "el" in empty_state_ref:
            empty_state_ref["el"].style("")

    def ui_progress_hook(desc, current, total, file_path=None, media_type=None):
        nonlocal speed_byte_window, last_known_bytes

        # Global speed tracking
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
                    .style("width: 100%; align-items: center; gap: 10px;")
                )
                row.style("order: 0;")
                with row:
                    name_label = ui.label(desc).style(
                        "font-size: 13px; font-weight: 500;"
                        " color: var(--text-secondary);"
                        " white-space: nowrap; overflow: hidden;"
                        " text-overflow: ellipsis; flex: 1; min-width: 0;"
                    )
                    bar = (
                        ui.linear_progress(value=0, show_value=False)
                        .props("instant-feedback color=primary size=6px rounded")
                        .style("width: 120px; border-radius: 3px;")
                    )
                    info_label = ui.label("").style(
                        "font-size: 12px; font-weight: 500;"
                        " color: var(--text-tertiary);"
                        " white-space: nowrap;"
                        " font-variant-numeric: tabular-nums;"
                    )
                    action_col = ui.column().style(
                        "min-width: 40px; align-items: flex-end;"
                    )
                active_downloads[desc] = [
                    row,
                    name_label,
                    bar,
                    info_label,
                    action_col,
                    now,
                    0,
                    True,  # visible
                    False,  # completed
                ]
                download_order.append(desc)
                _update_empty_state()
                # Max 4 visible: hide oldest completed first
                visible_count = sum(
                    1
                    for d in download_order
                    if d in active_downloads and active_downloads[d][7]
                )
                while visible_count > 4:
                    removed = None
                    for d in download_order:
                        if d in active_downloads and active_downloads[d][7]:
                            if active_downloads[d][8]:
                                removed = d
                                break
                    if removed is None:
                        for d in download_order:
                            if d in active_downloads and active_downloads[d][7]:
                                removed = d
                                break
                    if removed:
                        try:
                            active_downloads[removed][0].style("display: none;")
                        except Exception:
                            pass
                        active_downloads[removed][7] = False
                        visible_count -= 1

        entry = active_downloads[desc]
        row = entry[0]
        name_label = entry[1]
        bar = entry[2]
        info_label = entry[3]
        action_col = entry[4]
        start_time = entry[5]

        if total > 0:
            fraction = current / total
            bar.set_value(fraction)

            # ETA calculation
            elapsed = now - start_time
            if elapsed > 0.5 and current > 0:
                file_speed = current / elapsed
                remaining = total - current
                eta_sec = remaining / file_speed if file_speed > 0 else 0
                if eta_sec > 3600:
                    eta_str = f"{eta_sec / 3600:.1f}h left"
                elif eta_sec > 60:
                    eta_str = f"{eta_sec / 60:.0f}m left"
                elif eta_sec > 0:
                    eta_str = f"{eta_sec:.0f}s left"
                else:
                    eta_str = ""
            else:
                eta_str = ""

            pct_str = f"{fraction * 100:.0f}%"
            info_text = pct_str
            if eta_str:
                info_text += f" \u00b7 {eta_str}"
            info_label.set_text(info_text)

            if current >= total:
                entry[8] = True  # mark completed
                row.style("order: 1;")
                name_label.style(
                    "font-size: 13px; font-weight: 600;"
                    " color: var(--positive);"
                    " white-space: nowrap; overflow: hidden;"
                    " text-overflow: ellipsis; flex: 1; min-width: 0;"
                )
                if desc.startswith("Downloading "):
                    name_label.set_text(desc.replace("Downloading ", "\u2713 ", 1))
                info_label.set_text("Done")
                info_label.style("color: var(--positive);")

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

            active_downloads[desc][5] = start_time
            active_downloads[desc][6] = current
        else:
            bar.set_value(0)
            info_label.set_text("...")

    async def run_downloader():
        if is_running["value"]:
            ui.notify("Downloader is already running!", type="warning")
            return
        if is_monitoring["value"]:
            ui.notify(
                "Stop monitoring before starting history download.", type="warning"
            )
            return
        is_running["value"] = True
        switched_to_monitor = False
        media_downloader.PENDING_IDS.clear()
        media_downloader.FAILED_IDS.clear()
        media_downloader.DOWNLOADED_IDS.clear()
        media_downloader.PROCESSED_IDS.clear()
        media_downloader.CURRENT_BATCH_IDS.clear()
        media_downloader.BACKLOG_ITERATED.clear()
        media_downloader.BACKLOG_DONE.clear()
        main_logger = logging.getLogger("media_downloader")
        main_logger.addHandler(ui_logger)
        try:
            _log_widget().clear()
            progress_container.clear()
            active_downloads.clear()
            download_order.clear()
            speed_byte_window.clear()
            last_known_bytes.clear()
            _update_empty_state()
            update_status("Running", "status-running")
            ui.notify("Initializing Telegram Client...", type="info")
            media_downloader.UI_PROGRESS_HOOK = ui_progress_hook
            fresh_config = load_config_fn()
            stop_dl_btn.style("display: block;")
            updated_config = await media_downloader.begin_import(
                fresh_config, pagination_limit=100, client_ref=download_client_ref
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
                _log_widget().push(
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
                # Auto-switch to monitor if mode is history_monitor
                fresh2 = load_config_fn()
                if fresh2.get("mode") == "history_monitor":
                    _log_widget().push("Backlog complete. Switching to monitor mode...")
                    ui.notify("Switching to monitor mode...", type="info")
                    switched_to_monitor = True
                    is_running["value"] = False
                    await run_monitor()
                    return
        except Exception as e:
            update_status("Error", "status-error")
            _log_widget().push(f"Error: {str(e)}")
            ui.notify(f"Error: {str(e)}", type="negative", position="top")
        finally:
            if not switched_to_monitor:
                media_downloader.UI_PROGRESS_HOOK = None
                is_running["value"] = False
                main_logger.removeHandler(ui_logger)
                stop_dl_btn.style("display: none;")
                download_client_ref["client"] = None
                _update_empty_state()
            # Save progress even on error/stop
            fresh = load_config_fn()
            media_downloader.update_config(fresh)
            update_total_gb()

    async def run_monitor():
        if is_monitoring["value"]:
            ui.notify("Monitor is already running!", type="warning")
            return
        if is_running["value"]:
            ui.notify(
                "Wait for history download to finish before monitoring.", type="warning"
            )
            return
        is_monitoring["value"] = True
        media_downloader.PENDING_IDS.clear()
        media_downloader.FAILED_IDS.clear()
        media_downloader.DOWNLOADED_IDS.clear()
        media_downloader.PROCESSED_IDS.clear()
        media_downloader.CURRENT_BATCH_IDS.clear()
        media_downloader.BACKLOG_ITERATED.clear()
        media_downloader.BACKLOG_DONE.clear()
        main_logger = logging.getLogger("media_downloader")
        main_logger.addHandler(ui_logger)
        try:
            _log_widget().clear()
            progress_container.clear()
            active_downloads.clear()
            download_order.clear()
            speed_byte_window.clear()
            last_known_bytes.clear()
            _update_empty_state()
            update_status("Monitoring", "status-monitoring")
            ui.notify("Starting monitor mode...", type="info")
            media_downloader.UI_PROGRESS_HOOK = ui_progress_hook
            fresh_config = load_config_fn()
            client = await media_downloader.begin_monitor(fresh_config)
            monitor_client_ref["client"] = client
            stop_btn.style("display: block;")
            _log_widget().push("Monitor active. Listening for new media...")
            ui.notify(
                "Monitor mode active. Listening for new messages...",
                type="positive",
            )
        except Exception as e:
            is_monitoring["value"] = False
            update_status("Error", "status-error")
            _log_widget().push(f"Error: {str(e)}")
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
        stop_btn.style("display: none;")
        update_status("Idle", "status-idle")
        media_downloader.UI_PROGRESS_HOOK = None
        main_logger = logging.getLogger("media_downloader")
        try:
            main_logger.removeHandler(ui_logger)
        except Exception:
            pass
        ui.notify("Monitor stopped.", type="info")

    stop_monitoring_fn["fn"] = stop_monitoring

    async def stop_download():
        if download_client_ref["client"] is not None:
            try:
                await download_client_ref["client"].disconnect()
            except Exception:
                pass
            download_client_ref["client"] = None
        ui.notify("Download stopped. Progress saved.", type="info")

    stop_download_fn["fn"] = stop_download

    # Timers
    ui.timer(0.5, update_speed_display)

    def update_pending():
        if pending_label is not None:
            active = sum(media_downloader.PENDING_IDS.values())
            total_iter = sum(media_downloader.BACKLOG_ITERATED.values())
            total_done = sum(media_downloader.BACKLOG_DONE.values())
            queued = max(0, total_iter - total_done)
            pending_label.set_text(f"{active} / {queued}")
            if active > 0:
                pending_label.style("color: var(--accent);")
            else:
                pending_label.style("color: var(--text-secondary);")

    ui.timer(0.5, update_pending)
    ui.timer(1.0, update_total_gb)
    update_total_gb()
