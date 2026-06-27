"""Debug Report tab for exporting error reports."""

import datetime
import logging
import os
import platform
import sys

from nicegui import ui

import db
import media_downloader
from utils.obfuscate import obfuscate_chat_name, obfuscate_config


def build_debug_tab(config: dict, this_dir: str, log_area_holder: dict):
    """Build the Debug Report tab panel contents.

    Parameters
    ----------
    config : dict
        Loaded configuration dictionary.
    this_dir : str
        Absolute path to the project root directory.
    log_area_holder : dict
        Holder for the ui.log widget reference (for recent log lines).
    """
    with ui.column().style("gap: 2px; margin-bottom: 28px; align-items: center;"):
        ui.label("Debug Report").classes("section-title")
        ui.label("Generate an exportable report for troubleshooting.").classes(
            "section-subtitle"
        )

    report_output = (
        ui.textarea("Report")
        .style(
            "width: 100%; min-height: 400px; font-family: monospace;"
            " font-size: 12px; line-height: 1.5;"
        )
        .props("outlined readonly")
    )

    def _generate_report() -> str:
        safe = obfuscate_config(config)
        lines = []
        lines.append("=" * 60)
        lines.append("  Telegram Media Downloader — Debug Report")
        lines.append("  Generated: " + datetime.datetime.now().isoformat())
        lines.append("=" * 60)
        lines.append("")

        # System info
        lines.append("── System ──")
        lines.append(f"  Python      : {sys.version}")
        lines.append(f"  Platform    : {platform.platform()}")
        lines.append(f"  App version : v3.5.0")
        lines.append(f"  Session     : {safe.get('_session', '?')}")
        memory = 0
        try:
            import psutil

            memory = psutil.virtual_memory().available // (1024 * 1024)
        except Exception:
            pass
        if memory:
            lines.append(f"  RAM free    : {memory} MB")
        lines.append("")

        # Config snapshot
        lines.append("── Config (obfuscated) ──")
        lines.append(f"  api_id              : {safe.get('api_id', '?')}")
        lines.append(f"  api_hash            : {safe.get('api_hash', '?')}")
        lines.append(f"  phone               : {safe.get('phone', '?')}")
        lines.append(f"  chat_id             : {safe.get('chat_id', '?')}")
        lines.append(f"  mode                : {safe.get('mode', 'history')}")
        lines.append(
            f"  download_dir        : {safe.get('download_directory', '(app dir)')}"
        )
        lines.append(f"  delay               : {safe.get('download_delay', '?')}")
        lines.append(
            f"  max_concurrent      : {safe.get('max_concurrent_downloads', '?')}"
        )
        lines.append(f"  media_types         : {safe.get('media_types', [])}")
        lines.append("")

        # Chats
        chats = safe.get("chats", [])
        lines.append(f"── Chats ({len(chats)}) ──")
        for c in chats:
            cid = c.get("chat_id", "?")
            lines.append(f"  - {cid}")
        lines.append("")

        # DB stats
        lines.append("── Database ──")
        total_bytes = db.get_total_downloaded_bytes()
        counts = db.get_download_counts()
        lines.append(f"  Total downloaded : {db.format_bytes(total_bytes)}")
        lines.append(f"  Videos           : {counts.get('video', 0)}")
        lines.append(f"  Photos           : {counts.get('photo', 0)}")
        lines.append(f"  DB path          : downloads.sqlite3")
        lines.append("")

        # Recent errors
        errors = list(media_downloader._ERROR_LOG)
        lines.append(f"── Recent Errors ({len(errors)}) ──")
        if errors:
            for e in errors[-20:]:
                ts = e.get("time", "")
                level = e.get("level", "?")
                msg = e.get("message", "")
                func = e.get("funcName", "?")
                lineno = e.get("lineno", "?")
                lines.append(f"  [{ts}] {level}  {msg}")
                lines.append(f"         at {func}:{lineno}")
        else:
            lines.append("  (no errors captured)")
        lines.append("")

        # Recent log tail
        lines.append("── Recent Log ──")
        log_widget = (
            log_area_holder.get("widget")
            if isinstance(log_area_holder, dict)
            else log_area_holder
        )
        if log_widget is not None and hasattr(log_widget, "value"):
            tail = str(log_widget.value or "")
            for line in tail.split("\n")[-30:]:
                lines.append(f"  {line}")
        else:
            lines.append("  (log not available)")
        lines.append("")

        # Chat titles (obfuscated)
        titles = {
            k: obfuscate_chat_name(v) for k, v in media_downloader.CHAT_TITLES.items()
        }
        if titles:
            lines.append(f"── Chat Titles ({len(titles)}) ──")
            for cid, title in list(titles.items())[:20]:
                lines.append(f"  {cid[-6:]} → {title}")
        lines.append("")

        lines.append("=" * 60)
        lines.append("  End of Report")
        lines.append("=" * 60)
        return "\n".join(lines)

    # Actions row
    with ui.row().style("gap: 12px; margin-top: 16px;"):
        ui.button(
            "Generate Report",
            icon="description",
            on_click=lambda: report_output.set_value(_generate_report()),
        ).props('unelevated color="primary"').style("font-size: 13px;")

        def _copy_report():
            report = _generate_report()
            safe_text = report.replace("\\", "\\\\").replace("`", "\\`")
            safe_text = safe_text.replace("\n", "\\n").replace("\r", "")
            safe_text = safe_text.replace('"', '\\"')
            js_code = f'navigator.clipboard.writeText("{safe_text}")'
            ui.run_javascript(js_code)
            ui.notify("Report copied to clipboard", type="info")

        ui.button(
            "Copy to Clipboard",
            icon="content_copy",
            on_click=_copy_report,
        ).props("outline dense color=info").style("font-size: 13px;")

        def _download_report():
            report = _generate_report()
            path = os.path.join(
                this_dir, f"debug_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            ui.notify(f"Saved to {os.path.basename(path)}", type="positive")

        ui.button(
            "Save to File",
            icon="save",
            on_click=_download_report,
        ).props(
            "flat color=grey-7"
        ).style("font-size: 13px;")

    # Auto-generate on load
    report_output.set_value(_generate_report())
