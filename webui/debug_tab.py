"""Debug Report tab for live error monitoring and export."""

import datetime
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

    for _name in ("logging", "psutil"):
        pass  # keep import dedup happy

    with ui.column().style("gap: 2px; margin-bottom: 28px; align-items: center;"):
        ui.label("Debug Report").classes("section-title")
        ui.label("Live error monitoring and exportable diagnostic reports.").classes(
            "section-subtitle"
        )

    # ── Live Errors & Warnings ──
    with ui.element("div").classes("premium-card").style(
        "padding: 24px; margin-bottom: 16px;"
    ):
        with ui.row().classes("items-center").style("gap: 10px; margin-bottom: 12px;"):
            ui.icon("warning", size="sm", color="negative")
            ui.label("Errors & Warnings (live)").style(
                "font-size: 15px; font-weight: 600;" " color: var(--text-primary);"
            )
        live_errors = (
            ui.log(max_lines=30)
            .classes("terminal-log")
            .style(
                "width: 100%; min-height: 150px; max-height: 250px;"
                " padding: 12px; font-size: 12px; line-height: 1.6;"
                " font-family: monospace; overflow-y: auto;"
                " background: rgba(255,0,0,0.03); border: 1px solid var(--border);"
                " border-radius: 8px;"
            )
        )

        def _refresh_live_errors():
            errors = list(media_downloader._ERROR_LOG)
            if errors:
                lines = []
                for e in errors[-30:]:
                    ts = str(e.get("time", ""))[:19]
                    level = e.get("level", "?")
                    msg = e.get("message", "")
                    func = e.get("funcName", "?")
                    lineno = e.get("lineno", "?")
                    prefix = f"[{ts}] {level:7s}"
                    lines.append(f"{prefix} {msg}")
                    lines.append(f"{' ' * (len(prefix) + 1)}{func}:{lineno}")
                live_errors.content = "\n".join(lines)
            else:
                live_errors.content = "(no errors or warnings captured)"

        ui.timer(2.0, _refresh_live_errors)

    # ── Live Log Tail ──
    with ui.element("div").classes("premium-card").style(
        "padding: 24px; margin-bottom: 16px;"
    ):
        with ui.row().classes("items-center").style("gap: 10px; margin-bottom: 12px;"):
            ui.icon("terminal", size="sm", color="primary")
            ui.label("Recent Log (live)").style(
                "font-size: 15px; font-weight: 600;" " color: var(--text-primary);"
            )
        live_log = (
            ui.log(max_lines=30)
            .classes("terminal-log")
            .style(
                "width: 100%; min-height: 120px; max-height: 200px;"
                " padding: 12px; font-size: 12px; line-height: 1.6;"
                " font-family: monospace; overflow-y: auto;"
                " border: 1px solid var(--border);"
                " border-radius: 8px;"
            )
        )

        def _refresh_live_log():
            log_widget = (
                log_area_holder.get("widget")
                if isinstance(log_area_holder, dict)
                else log_area_holder
            )
            if log_widget is not None and hasattr(log_widget, "value"):
                tail = str(log_widget.value or "")
                live_log.content = "\n".join(tail.split("\n")[-30:])
            else:
                live_log.content = "(log not available)"

        ui.timer(2.0, _refresh_live_log)

    # ── Static Info ──
    with ui.element("div").classes("premium-card").style(
        "padding: 24px; margin-bottom: 16px;"
    ):
        with ui.row().classes("items-center").style("gap: 10px; margin-bottom: 12px;"):
            ui.icon("info", size="sm", color="primary")
            ui.label("System & Config").style(
                "font-size: 15px; font-weight: 600;" " color: var(--text-primary);"
            )
        static_info = ui.markdown("").style("font-size: 12px; line-height: 1.7;")

        def _refresh_static():
            safe = obfuscate_config(config)
            lines = []
            lines.append("**System**")
            lines.append(f"- Python: `{sys.version.split()[0]}`")
            lines.append(f"- Platform: `{platform.platform()}`")
            lines.append(f"- App version: `v3.5.0`")
            memory = 0
            try:
                import psutil

                memory = psutil.virtual_memory().available // (1024 * 1024)
            except Exception:
                pass
            if memory:
                lines.append(f"- RAM free: `{memory} MB`")
            lines.append("")
            lines.append("**Config (obfuscated)**")
            lines.append(f"- api_id: `{safe.get('api_id', '?')}`")
            lines.append(f"- api_hash: `{safe.get('api_hash', '?')}`")
            lines.append(f"- phone: `{safe.get('phone', '?')}`")
            lines.append(f"- chat_id: `{safe.get('chat_id', '?')}`")
            lines.append(f"- mode: `{safe.get('mode', 'history')}`")
            lines.append(
                f"- download_dir: `{safe.get('download_directory', '(app dir)')}`"
            )
            lines.append(f"- delay: `{safe.get('download_delay', '?')}`")
            lines.append(
                f"- max_concurrent: `{safe.get('max_concurrent_downloads', '?')}`"
            )
            lines.append(f"- media_types: `{safe.get('media_types', [])}`")
            lines.append("")
            lines.append("**Database**")
            total_bytes = db.get_total_downloaded_bytes()
            counts = db.get_download_counts()
            lines.append(f"- Total downloaded: `{db.format_bytes(total_bytes)}`")
            lines.append(f"- Videos: `{counts.get('video', 0)}`")
            lines.append(f"- Photos: `{counts.get('photo', 0)}`")
            lines.append("")
            titles = {
                k: obfuscate_chat_name(v)
                for k, v in media_downloader.CHAT_TITLES.items()
            }
            if titles:
                lines.append(f"**Chat Titles ({len(titles)})**")
                for cid, title in list(titles.items())[:10]:
                    lines.append(f"- `{cid[-6:]}` → `{title}`")
            static_info.set_content("\n".join(lines))

        _refresh_static()

    # ── Full Report (for export) ──
    report_output = (
        ui.textarea("Exportable Report")
        .style(
            "width: 100%; min-height: 200px; font-family: monospace;"
            " font-size: 12px; line-height: 1.5;"
        )
        .props("outlined readonly")
    )

    def _generate_full_report() -> str:
        safe = obfuscate_config(config)
        lines = []
        lines.append("=" * 60)
        lines.append("  Telegram Media Downloader — Debug Report")
        lines.append("  Generated: " + datetime.datetime.now().isoformat())
        lines.append("=" * 60)
        lines.append("")
        lines.append("── System ──")
        lines.append(f"  Python      : {sys.version}")
        lines.append(f"  Platform    : {platform.platform()}")
        lines.append(f"  App version : v3.5.0")
        lines.append(f"  Session     : {safe.get('_session', '?')}")
        lines.append("")
        lines.append("── Config (obfuscated) ──")
        for key in [
            "api_id",
            "api_hash",
            "phone",
            "chat_id",
            "mode",
            "download_directory",
            "download_delay",
            "max_concurrent_downloads",
            "media_types",
        ]:
            val = safe.get(key, "?")
            lines.append(f"  {key:24s}: {val}")
        lines.append("")
        chats = safe.get("chats", [])
        lines.append(f"── Chats ({len(chats)}) ──")
        for c in chats:
            lines.append(f"  - {c.get('chat_id', '?')}")
        lines.append("")
        lines.append("── Database ──")
        total_bytes = db.get_total_downloaded_bytes()
        counts = db.get_download_counts()
        lines.append(f"  Total downloaded : {db.format_bytes(total_bytes)}")
        lines.append(f"  Videos           : {counts.get('video', 0)}")
        lines.append(f"  Photos           : {counts.get('photo', 0)}")
        lines.append("")
        errors = list(media_downloader._ERROR_LOG)
        lines.append(f"── Errors & Warnings ({len(errors)}) ──")
        if errors:
            for e in errors[-50:]:
                ts = e.get("time", "")
                level = e.get("level", "?")
                msg = e.get("message", "")
                func = e.get("funcName", "?")
                lineno = e.get("lineno", "?")
                lines.append(f"  [{ts}] {level}  {msg}")
                lines.append(f"         at {func}:{lineno}")
        else:
            lines.append("  (none captured)")
        lines.append("")
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

    with ui.row().style("gap: 12px; margin-top: 16px;"):
        ui.button(
            "Generate Report",
            icon="description",
            on_click=lambda: report_output.set_value(_generate_full_report()),
        ).props('unelevated color="primary"').style("font-size: 13px;")

        def _copy_report():
            report = _generate_full_report()
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
            report = _generate_full_report()
            path = os.path.join(
                this_dir,
                f"debug_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt",
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

    report_output.set_value(_generate_full_report())
