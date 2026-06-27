"""Web UI for Telegram Media Downloader using NiceGUI."""

import asyncio
import logging
import os
import sqlite3
import sys

try:
    from nicegui import app, ui
except ImportError:
    print("\n[ERROR] Web UI dependencies are not installed.")
    print(
        "The Web UI is now an optional installation to keep the base "
        "application lightweight."
    )
    print("To use the graphical interface, please run:")
    print("  make install_webui")
    print("  or")
    print("  pip install -r requirements-webui.txt\n")
    sys.exit(1)

import media_downloader
from config_manager import load_config, save_config
from webui.config_tab import build_config_tab
from webui.debug_tab import build_debug_tab
from webui.execution_tab import build_execution_tab
from webui.history_tab import build_history_tab
from webui.setup_wizard import build_setup_wizard
from webui.styles import PREMIUM_CSS
from webui.tour import build_tour

logger = logging.getLogger("webui")
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PADDING_0 = "padding: 0;"

# Enable WAL mode on Telethon session file to reduce lock contention
# when verify buttons or other auxiliary clients access it concurrently
_session_path = os.path.join(THIS_DIR, "media_downloader.session")
if os.path.exists(_session_path):
    try:
        _sconn = sqlite3.connect(_session_path, timeout=5)
        _sconn.execute("PRAGMA journal_mode=WAL")
        _sconn.close()
    except Exception:
        pass

# Suppress Telethon connection cleanup noise on Python 3.13
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
# Suppress auto-reload noise ("X changes detected")
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logging.getLogger("nicegui").setLevel(logging.WARNING)
import warnings

warnings.filterwarnings("ignore", message=".*coroutine ignored GeneratorExit.*")
if hasattr(sys, "unraisablehook"):
    _original_hook = sys.unraisablehook
    sys.unraisablehook = lambda args: (
        None
        if "GeneratorExit" in str(getattr(args, "exc_value", ""))
        else _original_hook(args)
    )


@ui.page("/")
def index():  # NOSONAR
    ui.page_title("Telegram Media Downloader")
    config = load_config()
    ui.add_head_html(PREMIUM_CSS)
    dark_mode = ui.dark_mode()

    # ── First-run / auth detection ──
    session_exists = os.path.exists(os.path.join(THIS_DIR, "media_downloader.session"))

    def _valid_api(cfg):
        try:
            return bool(int(cfg.get("api_id", 0)) and cfg.get("api_hash", ""))
        except (TypeError, ValueError):
            return False

    has_api = _valid_api(config)
    has_chat = bool(config.get("chats") or config.get("chat_id"))

    if not has_api:
        ui.timer(
            0.3,
            lambda: build_setup_wizard(
                config, save_config, lambda: ui.navigate.reload(), start_step=1
            ),
            once=True,
        )
    elif not config.get("_wizard_completed"):
        ui.timer(
            0.3,
            lambda: build_setup_wizard(
                config, save_config, lambda: ui.navigate.reload(), start_step=1
            ),
            once=True,
        )
    elif not session_exists:
        ui.timer(
            0.3,
            lambda: build_setup_wizard(
                config, save_config, lambda: ui.navigate.reload(), start_step=2
            ),
            once=True,
        )
    elif not has_chat:
        ui.timer(
            0.3,
            lambda: build_setup_wizard(
                config, save_config, lambda: ui.navigate.reload(), start_step=3
            ),
            once=True,
        )

    current_page = {"value": "config"}
    log_area_holder = {}

    with ui.row().classes("w-full h-screen").style("margin:0; padding:0; gap:0;"):

        # ━━━━━ LEFT SIDEBAR ━━━━━
        with ui.column().classes("sidebar").style(
            "width: 260px; min-width: 260px; height: 100vh;"
            " position: sticky; top: 0; padding: 24px 16px;"
            " display: flex; flex-direction: column;"
            " justify-content: space-between;"
        ):
            with ui.column().style("gap: 4px;"):
                with ui.row().classes("items-center").style(
                    "gap: 10px; padding: 8px 16px 24px 16px;"
                ):
                    ui.icon("cloud_download", size="sm", color="primary")
                    with ui.column().style("gap: 0;"):
                        ui.label("TG Downloader").style(
                            "font-size: 15px; font-weight: 700;"
                            " letter-spacing: -0.025em;"
                            " color: var(--text-primary); line-height: 1.2;"
                        )
                        ui.label("Media Manager").style(
                            "font-size: 11px; font-weight: 500;"
                            " color: var(--text-tertiary);"
                            " letter-spacing: 0.02em;"
                        )

                ui.html('<hr class="divider" style="margin: 0 0 8px 0;">')
                ui.label("WORKSPACE").style(
                    "font-size: 10px; font-weight: 600;"
                    " color: var(--text-tertiary);"
                    " letter-spacing: 0.1em; padding: 0 16px 6px;"
                    " text-transform: uppercase;"
                )

                def make_nav(label, icon, page_key):
                    active = current_page["value"] == page_key
                    cls = "nav-item active" if active else "nav-item"
                    with ui.element("div").classes(cls) as nav:
                        ui.icon(icon)
                        ui.label(label)

                        def navigate(pk=page_key, _n=nav):
                            current_page["value"] = pk
                            tab_panels.set_value(pk)
                            for item, key in nav_items:
                                if key == pk:
                                    item.classes(replace="nav-item active")
                                else:
                                    item.classes(replace="nav-item")

                        nav.on("click", navigate)
                    return nav

                nav_items = []
                n1 = make_nav("Configuration", "tune", "config")
                nav_items.append((n1, "config"))
                n2 = make_nav("Execution", "play_circle_outline", "execution")
                nav_items.append((n2, "execution"))
                n3 = make_nav("History", "schedule", "history")
                nav_items.append((n3, "history"))
                n4 = make_nav("Terminal", "terminal", "terminal")
                nav_items.append((n4, "terminal"))
                n5 = make_nav("Debug", "bug_report", "debug")
                nav_items.append((n5, "debug"))

            with ui.column().style("gap: 8px; padding: 0 4px;"):
                ui.html('<hr class="divider" style="margin: 0;">')
                ui.button(
                    "Take Tour", on_click=lambda: show_tour(), icon="school"
                ).props("flat dense color=grey-7").style(
                    "width: 100%; justify-content: flex-start;"
                    " font-size: 13px; padding: 6px 12px;"
                )
                with ui.row().classes("items-center justify-between").style(
                    "padding: 4px 12px;"
                ):
                    ui.label("Dark mode").style(
                        "font-size: 13px; font-weight: 500;"
                        " color: var(--text-secondary);"
                    )
                    ui.switch(value=False, on_change=dark_mode.toggle).props("dense")

        # ━━━━━ MAIN CONTENT (tabs) ━━━━━
        with ui.column().style(
            "flex: 1; height: 100vh; padding: 32px 40px;"
            " overflow-y: auto; background: var(--surface-dim);"
        ):
            # Account badge (top-right)
            account_badge = ui.html(
                '<span class="status-badge status-free">\u2014 Account</span>'
            ).style("position: absolute; top: 16px; right: 40px; z-index: 10;")

            async def _check_account():
                try:
                    info = await media_downloader.check_account_premium(config)
                except Exception:
                    return
                if info is None:
                    return
                name = info.get("first_name", "")
                last = info.get("last_name", "")
                full = (name + " " + last).strip() or info.get("username", "?")
                if info.get("premium"):
                    account_badge.content = (
                        '<span class="status-badge status-premium">'
                        f"\u2b50 {full} (Premium)</span>"
                    )
                else:
                    account_badge.content = (
                        f'<span class="status-badge status-free">' f"{full}</span>"
                    )

            ui.timer(0.5, _check_account, once=True)

            # Media Viewing Modal
            with ui.dialog().props("maximized") as media_modal, ui.card().style(
                "background: #000; max-width: 900px; width: 90%;"
                " margin: auto; border-radius: var(--radius-xl);"
                " overflow: hidden; position: relative;"
            ):
                ui.button(icon="close", on_click=media_modal.close).props(
                    "flat dense round color=white"
                ).style("position: absolute; top: 12px; right: 12px; z-index: 50;")
                media_container = ui.column().style(
                    "width: 100%; min-height: 50vh; display: flex;"
                    " align-items: center; justify-content: center;"
                    " padding: 24px;"
                )

            def open_media(url: str, _filename: str):
                media_container.clear()
                ext = url.split(".")[-1].lower() if "." in url else ""
                with media_container:
                    if ext in ["mp4", "webm", "ogg"]:
                        ui.video(url).classes("w-full max-h-[80vh] object-contain")
                    elif ext in [
                        "jpg",
                        "jpeg",
                        "png",
                        "gif",
                        "webp",
                        "bmp",
                        "svg",
                    ]:
                        ui.image(url).classes("w-full max-h-[80vh] object-contain")
                    elif ext in ["mp3", "wav", "oga", "m4a", "flac"]:
                        ui.audio(url).classes("w-full q-mt-xl")
                    elif ext == "pdf":
                        ui.html(
                            f'<iframe src="{url}" width="100%" height="600px"'
                            f' style="border:none;border-radius:12px;"></iframe>'
                        ).classes("w-full")
                    else:
                        ui.label("Preview not available for this file type.").style(
                            "color: white; padding: 40px; font-size: 16px;"
                        )
                        ui.link("Download / Open Raw File", url, new_tab=True).style(
                            "color: #818cf8; font-size: 15px;"
                            " text-decoration: underline;"
                        )
                media_modal.open()

            with ui.tab_panels(value="config").classes("w-full").style(
                "background: transparent; padding: 0; margin: 0;"
            ).props("animated") as tab_panels:

                with ui.tab_panel("config").style(_PADDING_0):
                    _, chat_inputs = build_config_tab(config, save_config)

                with ui.tab_panel("execution").style(_PADDING_0):
                    build_execution_tab(
                        config,
                        load_config,
                        chat_inputs,
                        open_media,
                        THIS_DIR,
                        log_area_holder,
                    )

                with ui.tab_panel("history").style(_PADDING_0):
                    build_history_tab(config, open_media, THIS_DIR)

                with ui.tab_panel("terminal").style(_PADDING_0):
                    with ui.column().style(
                        "gap: 2px; margin-bottom: 28px; align-items: center;"
                    ):
                        ui.label("Terminal Output").classes("section-title")
                        ui.label(
                            "Real-time logs from downloads and monitor mode."
                        ).classes("section-subtitle")
                    log_area = (
                        ui.log(max_lines=500)
                        .classes("terminal-log")
                        .style(
                            "width: 100%; height: calc(100vh - 200px);"
                            " min-height: 480px; padding: 16px;"
                            " font-size: 13px; line-height: 1.7;"
                            " overflow-y: auto;"
                        )
                    )
                    log_area_holder["widget"] = log_area

                with ui.tab_panel("debug").style(_PADDING_0):
                    build_debug_tab(config, THIS_DIR, log_area_holder)

    # Build tour
    show_tour, check_first_visit = build_tour(current_page, tab_panels, nav_items)

    # Mount media files directory
    _init_cfg = load_config()
    _init_dl_dir = _init_cfg.get("download_directory", "")
    _base_media_path = os.path.abspath(_init_dl_dir) if _init_dl_dir else THIS_DIR
    if os.path.exists(_base_media_path):
        app.add_static_files("/media", _base_media_path)

    ui.timer(1.0, check_first_visit, once=True)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title="Telegram Media Downloader", port=8080, dark=False, show=False)
