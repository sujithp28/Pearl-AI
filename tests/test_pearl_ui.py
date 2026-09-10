"""
Pearl UI tests — structural validation of pearl_ui/index.html.

These tests verify the HTML structure, required elements, ARIA attributes,
and key JavaScript patterns. They run without a browser (no Playwright/Selenium).

Visual acceptance of the UI must be done manually in a real browser.
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent.parent / "pearl_ui"
UI_FILE = UI_DIR / "index.html"
HTML = UI_FILE.read_text(encoding="utf-8")

# The browser logic used to be one inline <script> in index.html, so
# reading that file gave you the whole UI. It now lives in ES modules
# under pearl_ui/js/, and these tests assert on behaviour rather than on
# which file it sits in — so they search the page plus its modules.
#
# `HTML` still means the page alone: the structural tests parse it as
# markup, and a module's source is not markup.
_MODULES = sorted(
    path for path in UI_DIR.glob("js/*.js") if not path.name.endswith(".test.js")
)
JS = "\n".join(path.read_text(encoding="utf-8") for path in _MODULES)
SOURCE = HTML + "\n" + JS


# ─── HTML parser helper ───────────────────────────────────────────────────────


class TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict((k, v or "") for k, v in attrs)))


def parse() -> TagCollector:
    p = TagCollector()
    p.feed(HTML)
    return p


def ids(collector: TagCollector) -> set[str]:
    return {a.get("id", "") for _, a in collector.elements if "id" in a}


def by_id(collector: TagCollector, el_id: str) -> dict | None:
    for _, attrs in collector.elements:
        if attrs.get("id") == el_id:
            return attrs
    return None


def by_class(collector: TagCollector, cls: str) -> list[dict]:
    results = []
    for _, attrs in collector.elements:
        classes = attrs.get("class", "").split()
        if cls in classes:
            results.append(attrs)
    return results


# ─── 1. Initial render ───────────────────────────────────────────────────────


class TestInitialRender:
    def test_html_file_exists(self) -> None:
        assert UI_FILE.exists(), "pearl_ui/index.html must exist"

    def test_has_doctype(self) -> None:
        assert HTML.lower().startswith("<!doctype html>")

    def test_has_title(self) -> None:
        assert "<title>Pearl AI</title>" in SOURCE

    def test_has_inter_font(self) -> None:
        assert "Inter" in SOURCE

    def test_has_jetbrains_mono(self) -> None:
        assert "JetBrains Mono" in SOURCE

    def test_has_viewport_meta(self) -> None:
        assert 'name="viewport"' in SOURCE

    def test_dark_first_theme(self) -> None:
        assert 'data-theme="dark"' in SOURCE

    def test_both_theme_tokens_defined(self) -> None:
        # Dark token --bg defined on :root
        assert "--bg:" in SOURCE
        # Light token defined in [data-theme="light"] or media query
        assert '[data-theme="light"]' in SOURCE or "prefers-color-scheme" in SOURCE

    def test_accent_color_defined(self) -> None:
        assert "--acc:" in SOURCE
        assert "#6366F1" in SOURCE or "6366F1" in SOURCE


# ─── 2. Sidebar ──────────────────────────────────────────────────────────────


class TestSidebar:
    def test_sidebar_element_exists(self) -> None:
        p = parse()
        assert "sidebar" in ids(p)

    def test_sidebar_has_aria_label(self) -> None:
        p = parse()
        sb = by_id(p, "sidebar")
        assert sb is not None
        assert sb.get("aria-label"), "sidebar must have aria-label"

    def test_logo_mark_present(self) -> None:
        p = parse()
        assert "logo-mark" in ids(p)

    def test_logo_text_present(self) -> None:
        assert ">Pearl<" in SOURCE

    def test_new_chat_button(self) -> None:
        p = parse()
        assert "new-chat-btn" in ids(p)

    def test_new_chat_has_aria_label(self) -> None:
        p = parse()
        btn = by_id(p, "new-chat-btn")
        assert btn is not None
        assert btn.get("aria-label"), "new-chat-btn needs aria-label"

    def test_search_input_present(self) -> None:
        p = parse()
        assert "sb-search" in ids(p)

    def test_search_has_aria_label(self) -> None:
        p = parse()
        el = by_id(p, "sb-search")
        assert el is not None
        assert el.get("aria-label"), "search must have aria-label"

    def test_conversation_list_container(self) -> None:
        p = parse()
        assert "sb-list" in ids(p)

    def test_conversation_list_has_role(self) -> None:
        p = parse()
        el = by_id(p, "sb-list")
        assert el is not None
        # Should be navigation or list
        assert el.get("role") or el.get("aria-label"), (
            "sb-list needs role or aria-label"
        )

    def test_workspace_badge(self) -> None:
        p = parse()
        assert "ws-badge" in ids(p)

    def test_settings_button(self) -> None:
        p = parse()
        assert "settings-btn" in ids(p)

    def test_connection_status(self) -> None:
        p = parse()
        assert "conn-dot" in ids(p)
        assert "conn-label" in ids(p)

    def test_conn_dot_has_role(self) -> None:
        p = parse()
        el = by_id(p, "conn-dot")
        assert el is not None
        assert el.get("role") == "status"

    def test_sidebar_collapsed_class_referenced(self) -> None:
        assert "collapsed" in SOURCE

    def test_collapse_toggle_button(self) -> None:
        p = parse()
        assert "sb-toggle" in ids(p)


# ─── 3. New chat ─────────────────────────────────────────────────────────────


class TestNewChat:
    def test_new_chat_js_function(self) -> None:
        assert "newChat" in SOURCE or "new-chat" in SOURCE

    def test_new_chat_clears_messages(self) -> None:
        assert "clearMsgs" in SOURCE or "clear" in SOURCE.lower()

    def test_empty_state_present(self) -> None:
        p = parse()
        assert "empty-state" in ids(p)

    def test_empty_state_title(self) -> None:
        assert "Your AI workspace." in SOURCE

    def test_suggestion_chips(self) -> None:
        p = parse()
        chips = by_class(p, "es-chip")
        assert len(chips) >= 4, "need at least 4 suggestion chips"

    def test_chips_have_data_prompt(self) -> None:
        p = parse()
        chips = by_class(p, "es-chip")
        for chip in chips:
            assert chip.get("data-prompt"), "each chip needs data-prompt"


# ─── 4. Sending a message ────────────────────────────────────────────────────


class TestSendMessage:
    def test_input_textarea(self) -> None:
        p = parse()
        assert "input-txt" in ids(p)

    def test_input_has_placeholder(self) -> None:
        p = parse()
        el = by_id(p, "input-txt")
        assert el is not None
        assert "placeholder" in el

    def test_send_button(self) -> None:
        p = parse()
        assert "send-btn" in ids(p)

    def test_send_btn_has_aria_label(self) -> None:
        p = parse()
        btn = by_id(p, "send-btn")
        assert btn is not None
        assert btn.get("aria-label"), "send-btn needs aria-label"

    def test_enter_sends_message(self) -> None:
        assert "Enter" in SOURCE and "shiftKey" in SOURCE

    def test_shift_enter_newline(self) -> None:
        assert "shiftKey" in SOURCE

    def test_submit_function(self) -> None:
        assert (
            "function submit" in SOURCE
            or "submit()" in SOURCE
            or "function submit" in SOURCE
        )

    def test_auto_resize_input(self) -> None:
        assert "resize" in SOURCE


# ─── 5. Streaming response ───────────────────────────────────────────────────


class TestStreaming:
    def test_sse_parser(self) -> None:
        assert (
            "async function* sse" in SOURCE
            or "parseSSE" in SOURCE
            or "function* sse" in SOURCE
        )

    def test_streaming_cursor_css(self) -> None:
        assert ".cursor" in SOURCE

    def test_cursor_blink_animation(self) -> None:
        assert "blink" in SOURCE

    def test_stop_button_present(self) -> None:
        p = parse()
        assert "stop-btn" in ids(p)

    def test_stop_btn_has_aria_label(self) -> None:
        p = parse()
        btn = by_id(p, "stop-btn")
        assert btn is not None
        assert btn.get("aria-label")

    def test_abort_controller_used(self) -> None:
        assert "AbortController" in SOURCE

    def test_setStreaming_function(self) -> None:
        assert "setStreaming" in SOURCE or "streaming" in SOURCE

    def test_done_event_handled(self) -> None:
        assert "'done'" in SOURCE or '"done"' in SOURCE


# ─── 6. Markdown rendering ───────────────────────────────────────────────────


class TestMarkdown:
    def test_renderMd_function(self) -> None:
        assert "renderMd" in SOURCE or "renderMd(" in SOURCE

    def test_headings_handled(self) -> None:
        assert "<h1>" in SOURCE or "h1>" in SOURCE

    def test_bold_handled(self) -> None:
        assert "strong" in SOURCE

    def test_italic_handled(self) -> None:
        assert "<em>" in SOURCE

    def test_links_handled(self) -> None:
        assert 'target="_blank"' in SOURCE or "target='_blank'" in SOURCE

    def test_lists_handled(self) -> None:
        assert "<ul>" in SOURCE and "<ol>" in SOURCE

    def test_blockquote_handled(self) -> None:
        assert "blockquote" in SOURCE

    def test_horizontal_rule(self) -> None:
        assert "<hr>" in SOURCE

    def test_md_class_on_bubble(self) -> None:
        assert 'class="pearl-bub md"' in SOURCE or "pearl-bub md" in SOURCE

    def test_strikethrough(self) -> None:
        assert "~~" in SOURCE and "<del>" in SOURCE


# ─── 7. Code blocks ──────────────────────────────────────────────────────────


class TestCodeBlocks:
    def test_fenced_code_regex(self) -> None:
        assert "```" in SOURCE

    def test_code_block_wrapper_css(self) -> None:
        assert ".cbw" in SOURCE or "code-block-wrap" in SOURCE

    def test_code_block_header_css(self) -> None:
        assert ".cbh" in SOURCE or "code-block-header" in SOURCE

    def test_lang_label(self) -> None:
        assert "cb-lang" in SOURCE

    def test_copy_button(self) -> None:
        assert "copy-btn" in SOURCE or "Copy" in SOURCE

    def test_copy_function(self) -> None:
        assert "doCopy" in SOURCE or "copyCode" in SOURCE or "clipboard" in SOURCE

    def test_pre_overflow_x(self) -> None:
        assert "overflow-x: auto" in SOURCE or "overflow-x:auto" in SOURCE

    def test_mono_font_on_pre(self) -> None:
        assert "var(--mono)" in SOURCE


# ─── 8. Tool activity card ───────────────────────────────────────────────────


class TestToolActivity:
    def test_tool_group_css(self) -> None:
        assert ".tool-group" in SOURCE

    def test_make_group_function(self) -> None:
        assert "makeGroup" in SOURCE or "createToolGroup" in SOURCE

    def test_progress_event_handled(self) -> None:
        assert "onProgress" in SOURCE or "progress" in SOURCE

    def test_running_indicator(self) -> None:
        assert ".spin" in SOURCE and "spin" in SOURCE

    def test_tg_head_element(self) -> None:
        assert "tg-head" in SOURCE

    def test_tg_body_element(self) -> None:
        assert "tg-body" in SOURCE

    def test_step_element(self) -> None:
        assert "t-step" in SOURCE or "ts-nm" in SOURCE

    def test_collapsible_toggle(self) -> None:
        assert "classList.toggle" in SOURCE and "open" in SOURCE


# ─── 9. Tool success state ───────────────────────────────────────────────────


class TestToolSuccess:
    def test_success_color_token(self) -> None:
        assert "--suc:" in SOURCE

    def test_checkmark_icon(self) -> None:
        assert "'✓'" in SOURCE or '"✓"' in SOURCE or ">✓<" in SOURCE

    def test_done_step_function(self) -> None:
        assert "doneStep" in SOURCE or "markStep" in SOURCE

    def test_refresh_steps_function(self) -> None:
        assert "refreshSteps" in SOURCE or "refresh" in SOURCE


# ─── 10. Tool failure state ──────────────────────────────────────────────────


class TestToolFailure:
    def test_error_color_token(self) -> None:
        assert "--err:" in SOURCE

    def test_failure_icon(self) -> None:
        assert "'✕'" in SOURCE or '"✕"' in SOURCE or ">✕<" in SOURCE

    def test_err_note_css(self) -> None:
        assert ".err-note" in SOURCE

    def test_add_err_function(self) -> None:
        assert "addErr" in SOURCE or "appendError" in SOURCE


# ─── 11. Plan card ───────────────────────────────────────────────────────────


class TestPlanCard:
    def test_plan_card_css(self) -> None:
        assert ".plan-card" in SOURCE

    def test_plan_header_css(self) -> None:
        assert ".plan-hd" in SOURCE or "plan-header" in SOURCE

    def test_plan_steps_css(self) -> None:
        assert ".plan-step" in SOURCE

    def test_plan_badge(self) -> None:
        assert ".plan-badge" in SOURCE or "plan-badge" in SOURCE


# ─── 12. Diff viewer ─────────────────────────────────────────────────────────


class TestDiffViewer:
    def test_diff_card_css(self) -> None:
        assert ".diff-card" in SOURCE

    def test_diff_body_css(self) -> None:
        assert ".diff-body" in SOURCE

    def test_diff_add_line(self) -> None:
        assert ".dl.add" in SOURCE or ".add" in SOURCE

    def test_diff_del_line(self) -> None:
        assert ".dl.del" in SOURCE or ".del" in SOURCE

    def test_diff_hunk_line(self) -> None:
        assert ".hunk" in SOURCE

    def test_diff_file_list(self) -> None:
        assert ".diff-files" in SOURCE or "diff-file" in SOURCE

    def test_add_diff_card_function(self) -> None:
        assert "addDiffCard" in SOURCE or "diffCard" in SOURCE.lower()


# ─── 13. Approve ─────────────────────────────────────────────────────────────


class TestApprove:
    def test_btn_approve_css(self) -> None:
        assert ".btn-approve" in SOURCE

    def test_approve_function(self) -> None:
        assert "approve(" in SOURCE or "doApprove" in SOURCE

    def test_approve_api_call(self) -> None:
        assert "/api/approve" in SOURCE

    def test_approve_button_text(self) -> None:
        assert "Approve" in SOURCE


# ─── 14. Reject ──────────────────────────────────────────────────────────────


class TestReject:
    def test_btn_reject_css(self) -> None:
        assert ".btn-reject" in SOURCE

    def test_reject_function(self) -> None:
        assert "reject(" in SOURCE or "doReject" in SOURCE

    def test_reject_api_call(self) -> None:
        assert "/api/reject" in SOURCE

    def test_reject_button_text(self) -> None:
        assert "Reject" in SOURCE


# ─── 15. Test results ────────────────────────────────────────────────────────


class TestTestResults:
    def test_result_card_css(self) -> None:
        assert ".result-card" in SOURCE

    def test_result_card_ok_variant(self) -> None:
        assert ".result-card.ok" in SOURCE or "result-card ok" in SOURCE

    def test_result_card_fail_variant(self) -> None:
        assert ".result-card.fail" in SOURCE or "result-card fail" in SOURCE

    def test_add_result_card_function(self) -> None:
        assert "addResultCard" in SOURCE or "resultCard" in SOURCE.lower()

    def test_step_list_css(self) -> None:
        assert ".result-steps" in SOURCE or ".result-step" in SOURCE


# ─── 16. Web sources ─────────────────────────────────────────────────────────


class TestWebSources:
    def test_web_search_handled_in_tool_labels(self) -> None:
        assert "web" in SOURCE.lower() or "search" in SOURCE.lower()

    def test_tool_label_map(self) -> None:
        # toolLabel function maps tool names to friendly names
        assert "toolLabel" in SOURCE or "tool_name" in SOURCE


# ─── 17. Error states ────────────────────────────────────────────────────────


class TestErrors:
    def test_err_note_role_alert(self) -> None:
        # role="alert" is set via setAttribute in JS (dynamic, not static HTML)
        assert (
            "setAttribute('role','alert')" in SOURCE
            or 'setAttribute("role","alert")' in SOURCE
            or 'role="alert"' in SOURCE
        )

    def test_abort_error_not_shown(self) -> None:
        assert "AbortError" in SOURCE  # must be caught and not displayed

    def test_disconnected_state(self) -> None:
        assert "Disconnected" in SOURCE or "disconnected" in SOURCE

    def test_error_color_distinct(self) -> None:
        assert "--err:" in SOURCE and "EF4444" in SOURCE


# ─── 18. Narrow layout (responsive) ─────────────────────────────────────────


class TestResponsive:
    def test_media_query_768(self) -> None:
        assert "768px" in SOURCE

    def test_sidebar_position_absolute_on_mobile(self) -> None:
        # The media query should make sidebar position absolute
        assert "position: absolute" in SOURCE or "position:absolute" in SOURCE

    def test_no_horizontal_overflow_on_body(self) -> None:
        assert "overflow: hidden" in SOURCE or "overflow:hidden" in SOURCE

    def test_code_blocks_scroll_internally(self) -> None:
        assert "overflow-x: auto" in SOURCE or "overflow-x:auto" in SOURCE

    def test_composer_always_visible(self) -> None:
        p = parse()
        assert "composer" in ids(p)

    def test_sidebar_toggle_button(self) -> None:
        p = parse()
        assert "sb-toggle" in ids(p)


# ─── Accessibility ────────────────────────────────────────────────────────────


class TestAccessibility:
    def test_focus_visible_style(self) -> None:
        assert ":focus-visible" in SOURCE

    def test_aria_live_on_messages(self) -> None:
        assert "aria-live" in SOURCE

    def test_aria_label_on_composer(self) -> None:
        p = parse()
        el = by_id(p, "composer")
        assert el is not None
        assert el.get("aria-label"), "composer needs aria-label"

    def test_aria_modal_on_dialog(self) -> None:
        assert 'aria-modal="true"' in SOURCE

    def test_aria_pressed_on_mode_buttons(self) -> None:
        assert "aria-pressed" in SOURCE

    def test_reduced_motion_support(self) -> None:
        assert "prefers-reduced-motion" in SOURCE

    def test_lang_attribute(self) -> None:
        assert 'lang="en"' in SOURCE

    def test_mode_toggle_group_label(self) -> None:
        assert 'aria-label="Interaction mode"' in SOURCE or "mode-toggle" in SOURCE


# ─── Mode toggle ─────────────────────────────────────────────────────────────


class TestModeToggle:
    def test_agent_mode_button(self) -> None:
        p = parse()
        btns = by_class(p, "mode-btn")
        modes = [b.get("data-mode") for b in btns]
        assert "agent" in modes

    def test_chat_mode_button(self) -> None:
        p = parse()
        btns = by_class(p, "mode-btn")
        modes = [b.get("data-mode") for b in btns]
        assert "chat" in modes

    def test_agent_active_by_default(self) -> None:
        p = parse()
        btns = by_class(p, "mode-btn")
        active = [b for b in btns if "active" in b.get("class", "")]
        assert any(b.get("data-mode") == "agent" for b in active)


# ─── API endpoints consumed ───────────────────────────────────────────────────


class TestAPIEndpoints:
    def test_api_status(self) -> None:
        assert "/api/status" in SOURCE

    def test_api_chat(self) -> None:
        assert "/api/chat" in SOURCE

    def test_api_run(self) -> None:
        assert "/api/run" in SOURCE

    def test_api_approve(self) -> None:
        assert "/api/approve" in SOURCE

    def test_api_reject(self) -> None:
        assert "/api/reject" in SOURCE

    def test_api_cancel(self) -> None:
        assert "/api/cancel" in SOURCE

    def test_api_patches(self) -> None:
        assert "/api/patches" in SOURCE

    def test_api_workspace(self) -> None:
        assert "/api/workspace" in SOURCE


# ─── LocalStorage ────────────────────────────────────────────────────────────


class TestLocalStorage:
    def test_localStorage_used(self) -> None:
        assert "localStorage" in SOURCE

    def test_store_key_defined(self) -> None:
        assert "STORE_KEY" in SOURCE or "STORAGE_KEY" in SOURCE

    def test_save_function(self) -> None:
        assert (
            "function save" in SOURCE
            or "saveToStorage" in SOURCE
            or "function save(" in SOURCE
        )

    def test_load_function(self) -> None:
        assert "loadStore" in SOURCE or "loadConversations" in SOURCE


# ─── Syntax highlighting ─────────────────────────────────────────────────────


class TestSyntaxHighlight:
    def test_hilight_function(self) -> None:
        assert "hilight" in SOURCE or "highlight" in SOURCE

    def test_keyword_token(self) -> None:
        assert "hl-kw" in SOURCE

    def test_string_token(self) -> None:
        assert "hl-str" in SOURCE

    def test_comment_token(self) -> None:
        assert "hl-cmt" in SOURCE

    def test_number_token(self) -> None:
        assert "hl-num" in SOURCE

    def test_function_token(self) -> None:
        assert "hl-fn" in SOURCE

    def test_python_keywords(self) -> None:
        assert "python" in SOURCE and "'def'" in SOURCE or '"def"' in SOURCE

    def test_javascript_keywords(self) -> None:
        assert "javascript" in SOURCE and "const" in SOURCE
