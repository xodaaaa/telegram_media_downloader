"""Setup wizard modal for first-run authentication."""

import asyncio

from nicegui import ui

import media_downloader

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

    def _safe_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    wizard_state = {
        "step": start_step,
        "api_id": _safe_int(config.get("api_id")),
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
        with content_area:
            ui.label("Enter the chat or channel you want to download from.").style(
                _TEXT_SUBTITLE
            )
            with ui.row().style("gap: 8px; align-items: flex-end;"):
                chat_in = (
                    ui.input(
                        "Chat ID / @username",
                        value=(
                            wizard_state.get("verified_name")
                            or wizard_state.get("chat_id", "")
                        ),
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

            # Browse My Chats toggle
            browse_state = {
                "open": False,
                "dialogs": [],
                "page": 0,
                "loading": False,
            }
            browse_btn_ref = {}
            browse_btn = (
                ui.button(
                    "Browse My Chats",
                    icon="list",
                    on_click=lambda: _toggle_browse(),
                )
                .props("flat dense color=grey-7")
                .style(
                    "font-size: 12px; width: 100%; justify-content: flex-start;"
                    " margin-top: 4px;"
                )
            )
            browse_btn_ref["btn"] = browse_btn
            browse_container = ui.column().style(
                "display: none; gap: 6px; padding: 8px;"
                " border: 1px solid var(--border); border-radius: 8px;"
                " margin-top: 6px; max-height: 260px; overflow-y: auto;"
            )
            with browse_container:
                browse_list = ui.column().style("gap: 2px;")
                with ui.row().style(
                    "justify-content: space-between; align-items: center;"
                    " padding: 4px 0; border-top: 1px solid var(--border);"
                ):
                    browse_page_label = ui.label("").style(
                        "font-size: 11px; color: var(--text-tertiary);"
                    )
                    with ui.row().style("gap: 4px;"):
                        browse_prev_btn = (
                            ui.button(
                                "Prev",
                                on_click=lambda: _browse_page(-1),
                            )
                            .props("flat dense size=sm color=grey-7")
                            .style("font-size: 11px;")
                        )
                        browse_next_btn = (
                            ui.button(
                                "Next",
                                on_click=lambda: _browse_page(1),
                            )
                            .props("flat dense size=sm color=grey-7")
                            .style("font-size: 11px;")
                        )

            ui.label(
                "Formats: @username for public channels, "
                "numeric ID for private groups (e.g. -1001234567890).\n"
                "Tip: forward a message to @RawDataBot"
                " on Telegram to get the ID."
            ).style("font-size: 11px; color: var(--text-tertiary);")

        async def _toggle_browse():
            if browse_state["open"]:
                browse_container.style("display: none;")
                browse_state["open"] = False
                browse_btn_ref["btn"].set_text("Browse My Chats")
                browse_btn_ref["btn"].props("flat dense color=grey-7")
                return
            if browse_state["loading"]:
                return
            browse_state["loading"] = True
            browse_btn_ref["btn"].set_text("Loading...")
            browse_btn_ref["btn"].set_enabled(False)
            dialogs = await media_downloader.get_user_dialogs(
                wizard_state["api_id"],
                wizard_state["api_hash"],
                wizard_state.get("client"),
            )
            browse_state["dialogs"] = dialogs
            browse_state["page"] = 0
            browse_state["loading"] = False
            browse_btn_ref["btn"].set_text("Hide My Chats")
            browse_btn_ref["btn"].props("flat dense color=positive")
            browse_btn_ref["btn"].set_enabled(True)
            if dialogs:
                browse_container.style("display: flex;")
                _render_browse_page()
            else:
                browse_state["open"] = True
                browse_btn_ref["btn"].set_text("Browse My Chats")
                browse_btn_ref["btn"].props("flat dense color=grey-7")
                verify_label.set_text("No chats found or unable to connect.")
                verify_label.style("color: var(--negative);")

        def _render_browse_page():
            browse_list.clear()
            dialogs = browse_state["dialogs"]
            page = browse_state["page"]
            per_page = 5
            start = page * per_page
            end = start + per_page
            page_items = dialogs[start:end]
            for d in page_items:
                icon = {
                    "channel": "campaign",
                    "group": "groups",
                    "bot": "smart_toy",
                    "user": "person",
                }.get(d["type"], "chat")
                with browse_list:
                    with ui.row().style("gap: 8px; align-items: center; width: 100%;"):
                        ui.icon(icon, size="xs").style("color: var(--text-tertiary);")
                        ui.label(d["name"]).style(
                            "font-size: 12px; color: var(--text-secondary);"
                            " flex: 1; white-space: nowrap; overflow: hidden;"
                            " text-overflow: ellipsis;"
                        )
                        ui.button(
                            "Select",
                            on_click=lambda d=d: _select_dialog(d),
                        ).props("flat dense size=sm color=primary").style(
                            "font-size: 11px;"
                        )
            total_pages = max(1, -(-len(dialogs) // per_page))  # ceil
            browse_page_label.set_text(
                f"Page {page + 1} of {total_pages} ({len(dialogs)} chats)"
            )
            browse_prev_btn.set_enabled(page > 0)
            browse_next_btn.set_enabled(end < len(dialogs))

        def _browse_page(delta):
            new_page = browse_state["page"] + delta
            if 0 <= new_page < max(1, -(-len(browse_state["dialogs"]) // 5)):
                browse_state["page"] = new_page
                _render_browse_page()

        def _select_dialog(dialog):
            wizard_state["chat_id"] = str(dialog["id"])
            wizard_state["verified_name"] = dialog["name"]
            chat_in.set_value(dialog["name"])
            chat_in.props(_PROPS_DENSE + ' color="positive"')
            verify_label.set_text("Chat selected")
            verify_label.style(
                "color: var(--positive); font-size: 12px; font-weight: 500;"
            )
            verify_btn_ref["btn"].set_text("Change")
            verify_btn_ref["btn"].props("outline dense color=positive")
            browse_container.style("display: none;")
            browse_state["open"] = False
            browse_btn_ref["btn"].set_text("Browse My Chats")
            browse_btn_ref["btn"].props("flat dense color=grey-7")

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
            verified = wizard_state.get("verified_name", "")
            if verified:
                chat_in.set_value(wizard_state["chat_id"])
                chat_in.props(_PROPS_DENSE)
                wizard_state["verified_name"] = ""
                verify_label.set_text("")
                verify_btn_ref["btn"].set_text("Verify")
                verify_btn_ref["btn"].props("outline dense color=info")
                return
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
                    name = getattr(entity, "title", None) or getattr(
                        entity, "first_name", None
                    )
                    if name:
                        last = getattr(entity, "last_name", "")
                        if last:
                            name = f"{name} {last}".strip()
                except Exception:
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
                    name = None
            if name:
                wizard_state["chat_id"] = str(chat_val)
                wizard_state["verified_name"] = name
                chat_in.set_value(name)
                chat_in.props(_PROPS_DENSE + ' color="positive"')
                verify_label.set_text("Chat detected")
                verify_label.style(
                    "color: var(--positive); font-size: 12px; font-weight: 500;"
                )
                verify_btn_ref["btn"].set_text("Change")
                verify_btn_ref["btn"].props("outline dense color=positive")
            else:
                verify_label.set_text("Could not resolve chat. Check the ID/username.")
                verify_label.style("color: var(--negative);")
            verify_btn_ref["btn"].set_enabled(True)

    def _go_back():
        wizard_state["step"] = max(1, wizard_state["step"] - 1)
        _render()

    def _finish(chat_val, skip=False):
        chat_val = str(chat_val).strip()
        if not skip and chat_val:
            original_id = wizard_state.get("chat_id", chat_val)
            try:
                chat_id_val = int(original_id)
            except ValueError:
                chat_id_val = original_id
            config["chat_id"] = chat_id_val
            config["chats"] = [
                {
                    "chat_id": chat_id_val,
                    "last_read_message_id": 0,
                    "ids_to_retry": [],
                }
            ]
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
        wizard_dialog.close()
        on_complete_fn()

    wizard_dialog.open()
    _render()
    return wizard_dialog
