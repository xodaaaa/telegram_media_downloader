"""Setup wizard modal for first-run authentication."""

from nicegui import ui

import media_downloader


def build_setup_wizard(
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

    result_label_ref = {}
    step_indicators = {}
    content_area = None
    footer_area = None

    total_steps = max(start_step, 3) if start_step <= 3 else start_step
    step_names = {1: "API Credentials", 2: "Phone Verification", 3: "Target Chat"}

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
                on_click=lambda: wizard_dialog.close(),
            ).props("flat dense round color=grey-6")

        # Progress indicators
        with ui.row().style(
            "gap: 12px; justify-content: center; padding: 12px 24px 0 24px;"
        ):
            for n in range(1, total_steps + 1):
                active = wizard_state["step"] == n
                past = wizard_state["step"] > n
                color = (
                    "var(--accent)"
                    if active
                    else ("var(--positive)" if past else "var(--text-tertiary)")
                )
                label = f"\u25cf {step_names[n]}"
                step_indicators[n] = ui.label(label).style(
                    f"font-size: 11px; font-weight: {'600' if active else '400'};"
                    f" color: {color}; letter-spacing: 0.02em;"
                )

        # Content area
        content_area = ui.column().style(
            "padding: 20px 24px; min-height: 200px; gap: 14px;"
        )

        # Result / error label
        with ui.row().style("justify-content: center; padding: 0 24px;"):
            result_label_ref["el"] = ui.label("").style(
                "font-size: 13px; font-weight: 500;"
            )

        # Footer
        footer_area = ui.row().style(
            "padding: 16px 24px; border-top: 1px solid var(--border);"
            " justify-content: space-between; width: 100%;"
        )

    def _render():
        content_area.clear()
        footer_area.clear()
        # Update progress indicators
        for n, lbl in step_indicators.items():
            active = wizard_state["step"] == n
            past = wizard_state["step"] > n
            color = (
                "var(--accent)"
                if active
                else ("var(--positive)" if past else "var(--text-tertiary)")
            )
            lbl.style(
                f"font-size: 11px; font-weight: {'600' if active else '400'};"
                f" color: {color}; letter-spacing: 0.02em;"
            )

        step = wizard_state["step"]
        result_label_ref["el"].set_text("")

        if step == 1:
            _render_step1()
        elif step == 2:
            _render_step2()
        elif step == 3:
            _render_step3()

    # ── Step 1: API Credentials ──
    def _render_step1():
        with content_area:
            ui.label(
                "Enter your Telegram API credentials.\n"
                "Get them at my.telegram.org \u2192 API Development Tools"
            ).style(
                "font-size: 13px; color: var(--text-secondary);" " line-height: 1.6;"
            )
            api_id_in = (
                ui.number(
                    "API ID",
                    value=wizard_state["api_id"] if wizard_state["api_id"] else None,
                    format="%.0f",
                )
                .classes("w-full")
                .props("outlined dense")
            )
            api_hash_in = (
                ui.input(
                    "API Hash",
                    value=wizard_state["api_hash"],
                    password=True,
                    password_toggle_button=True,
                )
                .classes("w-full")
                .props("outlined dense")
            )

        with footer_area:
            ui.element("div")  # spacer
            with ui.row().style("gap: 8px;"):
                ui.button(
                    "Next",
                    on_click=lambda: _go_step1(api_id_in.value, api_hash_in.value),
                ).props('unelevated color="primary"').style(
                    "font-size: 13px; padding: 6px 24px;"
                )

    def _go_step1(api_id_val, api_hash_val):
        try:
            api_id_int = int(api_id_val) if api_id_val is not None else 0
        except (TypeError, ValueError):
            api_id_int = 0
        if not api_id_int or not str(api_hash_val).strip():
            result_label_ref["el"].set_text(
                "\u26a0 Both API ID and API Hash are required."
            )
            result_label_ref["el"].style("color: var(--negative);")
            return
        wizard_state["api_id"] = api_id_int
        wizard_state["api_hash"] = str(api_hash_val).strip()
        config["api_id"] = api_id_int
        config["api_hash"] = str(api_hash_val).strip()
        wizard_state["step"] = 2
        _render()

    # ── Step 2: Phone Verification ──
    def _render_step2():
        nonlocal wizard_state
        with content_area:
            ui.label("We'll send a verification code to your phone.").style(
                "font-size: 13px; color: var(--text-secondary); line-height: 1.6;"
            )
            phone_in = (
                ui.input(
                    "Phone number",
                    value=wizard_state.get("phone", ""),
                    placeholder="+521234567890",
                )
                .classes("w-full")
                .props("outlined dense")
            )
            with ui.row().style("gap: 8px; align-items: center;"):
                code_in = (
                    ui.input("Verification code", placeholder="12345")
                    .classes("w-full")
                    .props("outlined dense")
                )

        with footer_area:
            ui.button("Back", on_click=lambda: _go_back()).props(
                "flat dense color=grey-7"
            ).style("font-size: 13px;")
            with ui.row().style("gap: 8px;"):
                ui.button(
                    "Send Code",
                    on_click=lambda: _send_code(phone_in.value, code_in),
                ).props("outline dense color=info").style("font-size: 13px;")
                ui.button(
                    "Verify",
                    on_click=lambda: _verify_code(phone_in.value, code_in.value),
                ).props('unelevated dense color="primary"').style(
                    "font-size: 13px; padding: 4px 20px;"
                )

    async def _send_code(phone, code_widget):
        phone = str(phone).strip()
        if not phone:
            result_label_ref["el"].set_text(
                "\u26a0 Enter a phone number in international format."
            )
            result_label_ref["el"].style("color: var(--negative);")
            return
        wizard_state["phone"] = phone
        result_label_ref["el"].set_text("\u23f3 Sending code...")
        result_label_ref["el"].style("color: var(--text-secondary);")
        result = await media_downloader.send_auth_code(
            wizard_state["api_id"], wizard_state["api_hash"], phone
        )
        if "error" in result:
            result_label_ref["el"].set_text(f"\u274c {result['error']}")
            result_label_ref["el"].style("color: var(--negative);")
            return
        wizard_state["client"] = result["client"]
        wizard_state["phone_code_hash"] = result["phone_code_hash"]
        result_label_ref["el"].set_text("\u2709 Code sent! Check your Telegram/sms.")
        result_label_ref["el"].style("color: var(--positive);")
        code_widget.run_method("focus")

    async def _verify_code(phone, code):
        phone = str(phone).strip()
        code = str(code).strip()
        if not phone or not code:
            result_label_ref["el"].set_text("\u26a0 Phone and code are required.")
            result_label_ref["el"].style("color: var(--negative);")
            return
        if wizard_state["client"] is None:
            result_label_ref["el"].set_text("\u26a0 Click 'Send Code' first.")
            result_label_ref["el"].style("color: var(--negative);")
            return
        result_label_ref["el"].set_text("\u23f3 Verifying...")
        result_label_ref["el"].style("color: var(--text-secondary);")
        ok = await media_downloader.verify_auth_code(
            wizard_state["client"],
            phone,
            code,
            wizard_state["phone_code_hash"],
        )
        if ok:
            config["phone"] = phone
            result_label_ref["el"].set_text("\u2705 Verified! Session saved.")
            result_label_ref["el"].style("color: var(--positive);")
            wizard_state["step"] = 3
            _render()
        else:
            result_label_ref["el"].set_text("\u274c Invalid code. Try again.")
            result_label_ref["el"].style("color: var(--negative);")

    # ── Step 3: Target Chat ──
    def _render_step3():
        with content_area:
            ui.label("Enter the chat or channel you want to download from.").style(
                "font-size: 13px; color: var(--text-secondary); line-height: 1.6;"
            )
            chat_in = (
                ui.input(
                    "Chat ID / @username",
                    value=wizard_state.get("chat_id", ""),
                    placeholder="123456789 or @channelname",
                )
                .classes("w-full")
                .props("outlined dense")
            )
            ui.label(
                "Tip: forward a message from the chat to @RawDataBot"
                " on Telegram to get the ID."
            ).style("font-size: 11px; color: var(--text-tertiary);")

        with footer_area:
            ui.button("Back", on_click=lambda: _go_back()).props(
                "flat dense color=grey-7"
            ).style("font-size: 13px;")
            with ui.row().style("gap: 8px;"):
                ui.button(
                    "Skip",
                    on_click=lambda: _finish(chat_in.value, skip=True),
                ).props("flat dense color=grey-7").style("font-size: 13px;")
                ui.button(
                    "Finish",
                    on_click=lambda: _finish(chat_in.value),
                ).props(
                    'unelevated color="primary"'
                ).style("font-size: 13px; padding: 6px 24px;")

    def _go_back():
        wizard_state["step"] = max(1, wizard_state["step"] - 1)
        _render()

    def _finish(chat_val, skip=False):
        chat_val = str(chat_val).strip()
        if not skip and chat_val:
            try:
                chat_id_val = int(chat_val)
            except ValueError:
                chat_id_val = chat_val
            config["chat_id"] = chat_id_val
            config["chats"] = [
                {
                    "chat_id": chat_id_val,
                    "last_read_message_id": 0,
                    "ids_to_retry": [],
                }
            ]
        # Ensure default pacing settings exist
        if "download_delay" not in config:
            config["download_delay"] = [15, 30]
        if "max_concurrent_downloads" not in config:
            config["max_concurrent_downloads"] = 4
        if "media_types" not in config:
            config["media_types"] = [
                "audio", "document", "photo", "video", "voice", "video_note"
            ]
        if "file_formats" not in config:
            config["file_formats"] = {
                "audio": ["all"],
                "document": ["all"],
                "video": ["all"],
            }
        if "phone" not in config and wizard_state.get("phone"):
            config["phone"] = wizard_state["phone"]
        save_config_fn(config)
        wizard_dialog.close()
        on_complete_fn()

    wizard_dialog.open()
    _render()
    return wizard_dialog
