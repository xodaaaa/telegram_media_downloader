"""Setup wizard modal for first-run authentication."""

import asyncio
import logging

from nicegui import ui
from telethon.utils import get_display_name

import media_downloader
from utils.parsing import safe_int

logger = logging.getLogger("wizard")

# Style constants
_PROPS_DENSE = "outlined dense"
_GAP_8 = "gap: 8px;"
_COLOR_NEG = "color: var(--negative);"
_COLOR_POS = "color: var(--positive);"
_COLOR_SEC = "color: var(--text-secondary);"
_FLAT_GREY = "flat dense color=grey-7"
_FONT_13 = "font-size: 13px;"
_TEXT_SUBTITLE = "font-size: 13px; color: var(--text-secondary); line-height: 1.6;"


def _step_color(active: bool, past: bool) -> str:
    """Return the CSS color for a wizard step indicator."""
    if active:
        return "var(--accent)"
    if past:
        return "var(--positive)"
    return "var(--text-tertiary)"


def build_setup_wizard(  # NOSONAR
    config: dict, save_config_fn, on_complete_fn, start_step: int = 1
):
    """Build and show a modal setup wizard dialog.

    Parameters
    ----------
    config : dict
        Mutable config dict updated in-place as the user fills fields.
    save_config_fn : callable
        ``save_config_fn(config)`` persists to disk.
    on_complete_fn : callable
        Called when the wizard finishes successfully.
    start_step : int
        1 = full wizard, 2 = phone only, 3 = chat only.
    """

    wizard_state = {
        "step": start_step,
        "api_id": safe_int(config.get("api_id"), default=0),
        "api_hash": (
            config.get("api_hash", "")
            if isinstance(config.get("api_hash"), str)
            else ""
        ),
        "phone": config.get("phone", ""),
        "client": None,
        "phone_code_hash": "",
        "chat_id": (str(config.get("chat_id", "")) if config.get("chat_id") else ""),
    }

    result_ref = {}
    step_indicators = {}
    content_area = None
    footer_area = None

    total_steps = max(start_step, 3) if start_step <= 3 else start_step
    step_names = {
        1: "API Credentials",
        2: "Phone Verification",
        3: "Target Chat",
    }

    with ui.dialog().props("persistent") as wizard_dialog, ui.card().style(
        "width: 480px; max-width: 90vw; border-radius: var(--radius-xl);"
        " overflow: hidden; background: var(--surface);"
        " margin: auto;"
        " border: 1px solid var(--border);"
    ):
        # Header
        with ui.row().classes("items-center justify-between").style(
            "padding: 20px 24px 0 24px;"
        ):
            ui.label("Setup Wizard").style(
                "font-size: 18px; font-weight: 700;"
                " color: var(--text-primary);"
                " letter-spacing: -0.01em;"
            )
            ui.button(
                icon="close",
                on_click=wizard_dialog.close,
            ).props("flat dense round color=grey-6")

        # Progress indicators
        with ui.row().style(
            "gap: 12px; justify-content: center; padding: 12px 24px 0 24px;"
        ):
            for n in range(1, total_steps + 1):
                active = wizard_state["step"] == n
                past = wizard_state["step"] > n
                color_ = _step_color(active, past)
                label = f"\u25cf {step_names[n]}"
                step_indicators[n] = ui.label(label).style(
                    f"font-size: 11px;"
                    f" font-weight: {'600' if active else '400'};"
                    f" color: {color_}; letter-spacing: 0.02em;"
                )

        # Content area
        content_area = ui.column().style(
            "padding: 20px 24px; min-height: 200px; gap: 14px;"
        )

        # Result / error label
        with ui.row().style("justify-content: center; padding: 0 24px;"):
            result_ref["el"] = ui.label("").style(_FONT_13 + " font-weight: 500;")

        # Footer
        footer_area = ui.row().style(
            "padding: 16px 24px;"
            " border-top: 1px solid var(--border);"
            " justify-content: space-between; width: 100%;"
        )

    def _set_result(text: str, style_color: str):
        result_ref["el"].set_text(text)
        result_ref["el"].style(style_color)

    def _render():
        content_area.clear()
        footer_area.clear()
        # Update progress indicators
        for n, lbl in step_indicators.items():
            active = wizard_state["step"] == n
            past = wizard_state["step"] > n
            color_ = _step_color(active, past)
            lbl.style(
                f"font-size: 11px;"
                f" font-weight: {'600' if active else '400'};"
                f" color: {color_}; letter-spacing: 0.02em;"
            )

        step = wizard_state["step"]
        result_ref["el"].set_text("")

        if step == 1:
            _render_step1()
        elif step == 2:
            _render_step2()
        elif step == 3:
            _render_step3()

    # ── Step 1 ──
    def _render_step1():
        with content_area:
            ui.label(
                "Enter your Telegram API credentials.\n"
                "Get them at my.telegram.org \u2192 API Development Tools"
            ).style(_TEXT_SUBTITLE)
            api_id_in = (
                ui.number(
                    "API ID",
                    value=(wizard_state["api_id"] if wizard_state["api_id"] else None),
                    format="%.0f",
                )
                .classes("w-full")
                .props(_PROPS_DENSE)
            )
            api_hash_in = (
                ui.input(
                    "API Hash",
                    value=wizard_state["api_hash"],
                    password=True,
                    password_toggle_button=True,
                )
                .classes("w-full")
                .props(_PROPS_DENSE)
            )

        with footer_area:
            ui.element("div")
            with ui.row().style(_GAP_8):
                ui.button(
                    "Next",
                    on_click=lambda: _go_step1(api_id_in.value, api_hash_in.value),
                ).props('unelevated color="primary"').style(
                    _FONT_13 + " padding: 6px 24px;"
                )

    def _go_step1(api_id_val, api_hash_val):
        try:
            api_id_int = int(api_id_val) if api_id_val is not None else 0
        except (TypeError, ValueError):
            api_id_int = 0
        if not api_id_int or not str(api_hash_val).strip():
            _set_result(
                "\u26a0 Both API ID and API Hash are required.",
                _COLOR_NEG,
            )
            return
        wizard_state["api_id"] = api_id_int
        wizard_state["api_hash"] = str(api_hash_val).strip()
        config["api_id"] = api_id_int
        config["api_hash"] = str(api_hash_val).strip()
        wizard_state["step"] = 2
        _render()

    # ── Step 2 ──
    def _render_step2():
        nonlocal wizard_state
        with content_area:
            ui.label("We'll send a verification code to your phone.").style(
                _TEXT_SUBTITLE
            )
            phone_in = (
                ui.input(
                    "Phone number",
                    value=wizard_state.get("phone", ""),
                    placeholder="+521234567890",
                )
                .classes("w-full")
                .props(_PROPS_DENSE)
            )
            with ui.row().style("gap: 8px; align-items: center;"):
                code_in = (
                    ui.input("Verification code", placeholder="12345")
                    .classes("w-full")
                    .props(_PROPS_DENSE)
                )

        with footer_area:
            ui.button("Back", on_click=_go_back).props(_FLAT_GREY).style(_FONT_13)
            with ui.row().style(_GAP_8):
                ui.button(
                    "Send Code",
                    on_click=lambda: _send_code(phone_in.value, code_in),
                ).props("outline dense color=info").style(_FONT_13)
                ui.button(
                    "Verify",
                    on_click=lambda: _verify_code(phone_in.value, code_in.value),
                ).props('unelevated dense color="primary"').style(
                    _FONT_13 + " padding: 4px 20px;"
                )

    async def _send_code(phone, code_widget):
        phone = str(phone).strip()
        if not phone:
            _set_result(
                "\u26a0 Enter a phone number in international format.",
                _COLOR_NEG,
            )
            return
        wizard_state["phone"] = phone
        _set_result("\u23f3 Sending code...", _COLOR_SEC)
        result = await media_downloader.send_auth_code(
            wizard_state["api_id"], wizard_state["api_hash"], phone
        )
        if "error" in result:
            _set_result(f"\u274c {result['error']}", _COLOR_NEG)
            return
        wizard_state["client"] = result["client"]
        wizard_state["phone_code_hash"] = result["phone_code_hash"]
        _set_result("\u2709 Code sent! Check your Telegram/sms.", _COLOR_POS)
        code_widget.run_method("focus")

    async def _verify_code(phone, code):
        phone = str(phone).strip()
        code = str(code).strip()
        if not phone or not code:
            _set_result("\u26a0 Phone and code are required.", _COLOR_NEG)
            return
        if wizard_state["client"] is None:
            _set_result("\u26a0 Click 'Send Code' first.", _COLOR_NEG)
            return
        _set_result("\u23f3 Verifying...", _COLOR_SEC)
        ok = await media_downloader.verify_auth_code(
            wizard_state["client"],
            phone,
            code,
            wizard_state["phone_code_hash"],
        )
        if ok:
            config["phone"] = phone
            _set_result("\u2705 Verified! Session saved.", _COLOR_POS)
            wizard_state["step"] = 3
            _render()
        else:
            _set_result("\u274c Invalid code. Try again.", _COLOR_NEG)

    # ── Step 3 ──
    def _render_step3():
        if "pending_chats" not in wizard_state:
            wizard_state["pending_chats"] = []

        with content_area:
            ui.label("Enter up to 3 chats or channels you want to download from.").style(
                _TEXT_SUBTITLE
            )

            # Show already-added chats
            pending_list = ui.column().style("gap: 4px; width: 100%;")
            def _refresh_pending():
                pending_list.clear()
                for i, pc in enumerate(wizard_state["pending_chats"]):
                    idx = i
                    with pending_list:
                        with ui.row().style("gap: 6px; align-items: center;"):
                            ui.label(pc["name"]).style(
                                "font-size: 13px; color: var(--positive); font-weight: 500;"
                            )
                            ui.button(
                                icon="close", on_click=lambda idx=idx: _remove_pending(idx)
                            ).props("flat dense round size=sm color=grey-6")
                if wizard_state["pending_chats"]:
                    ui.separator().style("opacity: 0.3; margin: 4px 0;")

            def _remove_pending(idx):
                wizard_state["pending_chats"].pop(idx)
                _refresh_pending()
                if len(wizard_state["pending_chats"]) < 3:
                    _add_row.set_visibility(True)
                    _max_label.set_visibility(False)

            _refresh_pending()

            # Input row (hidden when 3 chats added)
            _add_row = ui.row().style("gap: 8px; align-items: flex-end;")
            with _add_row:
                chat_in = (
                    ui.input(
                        "Chat ID / @username",
                        value="",
                        placeholder="123456789 or @channelname",
                    )
                    .classes("col")
                    .props(_PROPS_DENSE)
                )
                verify_btn_ref = {}
                _verify_btn = (
                    ui.button(
                        "Verify",
                        on_click=lambda: _toggle_verify(),
                    )
                    .props("outline dense color=info")
                    .style(_FONT_13)
                )
                verify_btn_ref["btn"] = _verify_btn
            verify_label = ui.label("").style("font-size: 12px; font-weight: 500;")
            _max_label = ui.label("Maximum 3 chats added.").style(
                "display: none; font-size: 11px; color: var(--text-tertiary);"
            )
            if len(wizard_state["pending_chats"]) >= 3:
                _add_row.set_visibility(False)
                _max_label.set_visibility(True)

            ui.label(
                "Formats: @username for public channels, "
                "numeric ID for private groups (e.g. -1001234567890).\n"
                "Tip: forward a message to @RawDataBot"
                " on Telegram to get the ID."
            ).style("font-size: 11px; color: var(--text-tertiary);")

        with footer_area:
            ui.button("Back", on_click=_go_back).props(_FLAT_GREY).style(_FONT_13)
            with ui.row().style(_GAP_8):
                ui.button(
                    "Skip",
                    on_click=lambda: _finish(chat_in.value, skip=True),
                ).props(_FLAT_GREY).style(_FONT_13)
                ui.button(
                    "Finish",
                    on_click=lambda: _finish(chat_in.value),
                ).props(
                    'unelevated color="primary"'
                ).style(_FONT_13 + " padding: 6px 24px;")

        async def _toggle_verify():
            chat_val = str(chat_in.value).strip()
            if not chat_val:
                verify_label.set_text("Enter a chat ID or @username first.")
                verify_label.style("color: var(--text-tertiary);")
                return
            verify_btn_ref["btn"].set_enabled(False)
            verify_label.set_text("Verifying...")
            verify_label.style("color: var(--text-secondary);")
            try:
                chat_id_val = int(chat_val)
            except ValueError:
                chat_id_val = chat_val
            name = None
            wiz_client = wizard_state.get("client")
            if wiz_client is not None:
                try:
                    entity = await asyncio.wait_for(
                        wiz_client.get_entity(chat_id_val), timeout=8.0
                    )
                    name = get_display_name(entity) or None
                except Exception:
                    logger.debug("Local entity name resolution failed, falling back")
                    name = None
            if not name:
                try:
                    name = await asyncio.wait_for(
                        media_downloader.resolve_chat_entity(
                            wizard_state["api_id"],
                            wizard_state["api_hash"],
                            chat_id_val,
                        ),
                        timeout=8.0,
                    )
                except Exception:
                    logger.warning("resolve_chat_entity timeout/error for %s", chat_id_val)
                    name = None
            if name:
                wizard_state["pending_chats"].append({
                    "chat_id": str(chat_val),
                    "name": name,
                })
                chat_in.set_value("")
                chat_in.props(_PROPS_DENSE)
                verify_label.set_text("Chat added")
                verify_label.style(
                    "color: var(--positive); font-size: 12px; font-weight: 500;"
                )
                _refresh_pending()
                if len(wizard_state["pending_chats"]) >= 3:
                    _add_row.set_visibility(False)
                    _max_label.set_visibility(True)
                if len(config.get("chats", [])) + len(wizard_state["pending_chats"]) >= 4:
                    ui.notify(
                        "Parallel Downloads is not recommended with 4+ chats.",
                        type="warning",
                        position="top",
                    )
            else:
                verify_label.set_text("Could not resolve chat. Check the ID/username.")
                verify_label.style("color: var(--negative);")
            verify_btn_ref["btn"].set_enabled(True)

    def _go_back():
        wizard_state["step"] = max(1, wizard_state["step"] - 1)
        _render()

    def _finish(chat_val, skip=False):
        if not skip and wizard_state["pending_chats"]:
            config["chats"] = []
            for pc in wizard_state["pending_chats"]:
                chat_id_val = pc["chat_id"]
                try:
                    chat_id_val = int(chat_id_val)
                except ValueError:
                    pass
                config["chats"].append({
                    "chat_id": chat_id_val,
                    "last_read_message_id": 0,
                    "ids_to_retry": [],
                })
            if config["chats"]:
                config["chat_id"] = config["chats"][0]["chat_id"]
        elif skip:
            pass
        if "download_delay" not in config:
            config["download_delay"] = 20
        if "max_concurrent_downloads" not in config:
            config["max_concurrent_downloads"] = 1
        if "media_types" not in config:
            config["media_types"] = [
                "audio",
                "document",
                "photo",
                "video",
                "voice",
                "video_note",
            ]
        if "file_formats" not in config:
            config["file_formats"] = {
                "audio": ["all"],
                "document": ["all"],
                "video": ["all"],
            }
        if "phone" not in config and wizard_state.get("phone"):
            config["phone"] = wizard_state["phone"]
        config["_wizard_completed"] = True
        save_config_fn(config)
        # Disconnect wizard client to release session for monitor/download
        wiz_client = wizard_state.get("client")
        if wiz_client is not None:
            try:
                asyncio.ensure_future(wiz_client.disconnect())
            except Exception:
                pass
        wizard_dialog.close()
        on_complete_fn()

    wizard_dialog.open()
    _render()
    return wizard_dialog
