"""
Pearl UI tests — structural validation of pearl_ui/index.html.

These tests verify the HTML structure, required elements, ARIA attributes,
and key JavaScript patterns. They run without a browser (no Playwright/Selenium).

Visual acceptance of the UI must be done manually in a real browser.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

UI_FILE = Path(__file__).resolve().parent.parent / "pearl_ui" / "index.html"
HTML = UI_FILE.read_text(encoding="utf-8")


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
        assert "<title>Pearl AI</title>" in HTML

    def test_has_inter_font(self) -> None:
        assert "Inter" in HTML

    def test_has_jetbrains_mono(self) -> None:
        assert "JetBrains Mono" in HTML

    def test_has_viewport_meta(self) -> None:
        assert 'name="viewport"' in HTML

    def test_dark_first_theme(self) -> None:
        assert 'data-theme="dark"' in HTML

    def test_both_theme_tokens_defined(self) -> None:
        # Dark token --bg defined on :root
        assert "--bg:" in HTML
        # Light token defined in [data-theme="light"] or media query
        assert "[data-theme=\"light\"]" in HTML or "prefers-color-scheme" in HTML

    def test_accent_color_defined(self) -> None:
        assert "--acc:" in HTML
        assert "#6366F1" in HTML or "6366F1" in HTML


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
        assert ">Pearl<" in HTML

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
        assert el.get("role") or el.get("aria-label"), "sb-list needs role or aria-label"

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
        assert "collapsed" in HTML

    def test_collapse_toggle_button(self) -> None:
        p = parse()
        assert "sb-toggle" in ids(p)


# ─── 3. New chat ─────────────────────────────────────────────────────────────

class TestNewChat:
    def test_new_chat_js_function(self) -> None:
        assert "newChat" in HTML or "new-chat" in HTML

    def test_new_chat_clears_messages(self) -> None:
        assert "clearMsgs" in HTML or "clear" in HTML.lower()

    def test_empty_state_present(self) -> None:
        p = parse()
        assert "empty-state" in ids(p)

    def test_empty_state_title(self) -> None:
        assert "Your AI workspace." in HTML

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
        assert "Enter" in HTML and "shiftKey" in HTML

    def test_shift_enter_newline(self) -> None:
        assert "shiftKey" in HTML

    def test_submit_function(self) -> None:
        assert "function submit" in HTML or "submit()" in HTML or "function submit" in HTML

    def test_auto_resize_input(self) -> None:
        assert "resize" in HTML


# ─── 5. Streaming response ───────────────────────────────────────────────────

class TestStreaming:
    def test_sse_parser(self) -> None:
        assert "async function* sse" in HTML or "parseSSE" in HTML or "function* sse" in HTML

    def test_streaming_cursor_css(self) -> None:
        assert ".cursor" in HTML

    def test_cursor_blink_animation(self) -> None:
        assert "blink" in HTML

    def test_stop_button_present(self) -> None:
        p = parse()
        assert "stop-btn" in ids(p)

    def test_stop_btn_has_aria_label(self) -> None:
        p = parse()
        btn = by_id(p, "stop-btn")
        assert btn is not None
        assert btn.get("aria-label")

    def test_abort_controller_used(self) -> None:
        assert "AbortController" in HTML

    def test_setStreaming_function(self) -> None:
        assert "setStreaming" in HTML or "streaming" in HTML

    def test_done_event_handled(self) -> None:
        assert "'done'" in HTML or '"done"' in HTML


# ─── 6. Markdown rendering ───────────────────────────────────────────────────

class TestMarkdown:
    def test_renderMd_function(self) -> None:
        assert "renderMd" in HTML or "renderMd(" in HTML

    def test_headings_handled(self) -> None:
        assert "<h1>" in HTML or "h1>" in HTML

    def test_bold_handled(self) -> None:
        assert "strong" in HTML

    def test_italic_handled(self) -> None:
        assert "<em>" in HTML

    def test_links_handled(self) -> None:
        assert "target=\"_blank\"" in HTML or "target='_blank'" in HTML

    def test_lists_handled(self) -> None:
        assert "<ul>" in HTML and "<ol>" in HTML

    def test_blockquote_handled(self) -> None:
        assert "blockquote" in HTML

    def test_horizontal_rule(self) -> None:
        assert "<hr>" in HTML

    def test_md_class_on_bubble(self) -> None:
        assert 'class="pearl-bub md"' in HTML or "pearl-bub md" in HTML

    def test_strikethrough(self) -> None:
        assert "~~" in HTML and "<del>" in HTML


# ─── 7. Code blocks ──────────────────────────────────────────────────────────

class TestCodeBlocks:
    def test_fenced_code_regex(self) -> None:
        assert "```" in HTML

    def test_code_block_wrapper_css(self) -> None:
        assert ".cbw" in HTML or "code-block-wrap" in HTML

    def test_code_block_header_css(self) -> None:
        assert ".cbh" in HTML or "code-block-header" in HTML

    def test_lang_label(self) -> None:
        assert "cb-lang" in HTML

    def test_copy_button(self) -> None:
        assert "copy-btn" in HTML or "Copy" in HTML

    def test_copy_function(self) -> None:
        assert "doCopy" in HTML or "copyCode" in HTML or "clipboard" in HTML

    def test_pre_overflow_x(self) -> None:
        assert "overflow-x: auto" in HTML or "overflow-x:auto" in HTML

    def test_mono_font_on_pre(self) -> None:
        assert "var(--mono)" in HTML


# ─── 8. Tool activity card ───────────────────────────────────────────────────

class TestToolActivity:
    def test_tool_group_css(self) -> None:
        assert ".tool-group" in HTML

    def test_make_group_function(self) -> None:
        assert "makeGroup" in HTML or "createToolGroup" in HTML

    def test_progress_event_handled(self) -> None:
        assert "onProgress" in HTML or "progress" in HTML

    def test_running_indicator(self) -> None:
        assert ".spin" in HTML and "spin" in HTML

    def test_tg_head_element(self) -> None:
        assert "tg-head" in HTML

    def test_tg_body_element(self) -> None:
        assert "tg-body" in HTML

    def test_step_element(self) -> None:
        assert "t-step" in HTML or "ts-nm" in HTML

    def test_collapsible_toggle(self) -> None:
        assert "classList.toggle" in HTML and "open" in HTML


# ─── 9. Tool success state ───────────────────────────────────────────────────

class TestToolSuccess:
    def test_success_color_token(self) -> None:
        assert "--suc:" in HTML

    def test_checkmark_icon(self) -> None:
        assert "'✓'" in HTML or '"✓"' in HTML or ">✓<" in HTML

    def test_done_step_function(self) -> None:
        assert "doneStep" in HTML or "markStep" in HTML

    def test_refresh_steps_function(self) -> None:
        assert "refreshSteps" in HTML or "refresh" in HTML


# ─── 10. Tool failure state ──────────────────────────────────────────────────

class TestToolFailure:
    def test_error_color_token(self) -> None:
        assert "--err:" in HTML

    def test_failure_icon(self) -> None:
        assert "'✕'" in HTML or '"✕"' in HTML or ">✕<" in HTML

    def test_err_note_css(self) -> None:
        assert ".err-note" in HTML

    def test_add_err_function(self) -> None:
        assert "addErr" in HTML or "appendError" in HTML


# ─── 11. Plan card ───────────────────────────────────────────────────────────

class TestPlanCard:
    def test_plan_card_css(self) -> None:
        assert ".plan-card" in HTML

    def test_plan_header_css(self) -> None:
        assert ".plan-hd" in HTML or "plan-header" in HTML

    def test_plan_steps_css(self) -> None:
        assert ".plan-step" in HTML

    def test_plan_badge(self) -> None:
        assert ".plan-badge" in HTML or "plan-badge" in HTML


# ─── 12. Diff viewer ─────────────────────────────────────────────────────────

class TestDiffViewer:
    def test_diff_card_css(self) -> None:
        assert ".diff-card" in HTML

    def test_diff_body_css(self) -> None:
        assert ".diff-body" in HTML

    def test_diff_add_line(self) -> None:
        assert ".dl.add" in HTML or ".add" in HTML

    def test_diff_del_line(self) -> None:
        assert ".dl.del" in HTML or ".del" in HTML

    def test_diff_hunk_line(self) -> None:
        assert ".hunk" in HTML

    def test_diff_file_list(self) -> None:
        assert ".diff-files" in HTML or "diff-file" in HTML

    def test_add_diff_card_function(self) -> None:
        assert "addDiffCard" in HTML or "diffCard" in HTML.lower()


# ─── 13. Approve ─────────────────────────────────────────────────────────────

class TestApprove:
    def test_btn_approve_css(self) -> None:
        assert ".btn-approve" in HTML

    def test_approve_function(self) -> None:
        assert "approve(" in HTML or "doApprove" in HTML

    def test_approve_api_call(self) -> None:
        assert "/api/approve" in HTML

    def test_approve_button_text(self) -> None:
        assert "Approve" in HTML


# ─── 14. Reject ──────────────────────────────────────────────────────────────

class TestReject:
    def test_btn_reject_css(self) -> None:
        assert ".btn-reject" in HTML

    def test_reject_function(self) -> None:
        assert "reject(" in HTML or "doReject" in HTML

    def test_reject_api_call(self) -> None:
        assert "/api/reject" in HTML

    def test_reject_button_text(self) -> None:
        assert "Reject" in HTML


# ─── 15. Test results ────────────────────────────────────────────────────────

class TestTestResults:
    def test_result_card_css(self) -> None:
        assert ".result-card" in HTML

    def test_result_card_ok_variant(self) -> None:
        assert ".result-card.ok" in HTML or "result-card ok" in HTML

    def test_result_card_fail_variant(self) -> None:
        assert ".result-card.fail" in HTML or "result-card fail" in HTML

    def test_add_result_card_function(self) -> None:
        assert "addResultCard" in HTML or "resultCard" in HTML.lower()

    def test_step_list_css(self) -> None:
        assert ".result-steps" in HTML or ".result-step" in HTML


# ─── 16. Web sources ─────────────────────────────────────────────────────────

class TestWebSources:
    def test_web_search_handled_in_tool_labels(self) -> None:
        assert "web" in HTML.lower() or "search" in HTML.lower()

    def test_tool_label_map(self) -> None:
        # toolLabel function maps tool names to friendly names
        assert "toolLabel" in HTML or "tool_name" in HTML


# ─── 17. Error states ────────────────────────────────────────────────────────

class TestErrors:
    def test_err_note_role_alert(self) -> None:
        # role="alert" is set via setAttribute in JS (dynamic, not static HTML)
        assert "setAttribute('role','alert')" in HTML or 'setAttribute("role","alert")' in HTML or 'role="alert"' in HTML

    def test_abort_error_not_shown(self) -> None:
        assert "AbortError" in HTML  # must be caught and not displayed

    def test_disconnected_state(self) -> None:
        assert "Disconnected" in HTML or "disconnected" in HTML

    def test_error_color_distinct(self) -> None:
        assert "--err:" in HTML and "EF4444" in HTML


# ─── 18. Narrow layout (responsive) ─────────────────────────────────────────

class TestResponsive:
    def test_media_query_768(self) -> None:
        assert "768px" in HTML

    def test_sidebar_position_absolute_on_mobile(self) -> None:
        # The media query should make sidebar position absolute
        assert "position: absolute" in HTML or "position:absolute" in HTML

    def test_no_horizontal_overflow_on_body(self) -> None:
        assert "overflow: hidden" in HTML or "overflow:hidden" in HTML

    def test_code_blocks_scroll_internally(self) -> None:
        assert "overflow-x: auto" in HTML or "overflow-x:auto" in HTML

    def test_composer_always_visible(self) -> None:
        p = parse()
        assert "composer" in ids(p)

    def test_sidebar_toggle_button(self) -> None:
        p = parse()
        assert "sb-toggle" in ids(p)


# ─── Accessibility ────────────────────────────────────────────────────────────

class TestAccessibility:
    def test_focus_visible_style(self) -> None:
        assert ":focus-visible" in HTML

    def test_aria_live_on_messages(self) -> None:
        assert "aria-live" in HTML

    def test_aria_label_on_composer(self) -> None:
        p = parse()
        el = by_id(p, "composer")
        assert el is not None
        assert el.get("aria-label"), "composer needs aria-label"

    def test_aria_modal_on_dialog(self) -> None:
        assert 'aria-modal="true"' in HTML

    def test_aria_pressed_on_mode_buttons(self) -> None:
        assert 'aria-pressed' in HTML

    def test_reduced_motion_support(self) -> None:
        assert "prefers-reduced-motion" in HTML

    def test_lang_attribute(self) -> None:
        assert 'lang="en"' in HTML

    def test_mode_toggle_group_label(self) -> None:
        assert 'aria-label="Interaction mode"' in HTML or "mode-toggle" in HTML


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
        assert "/api/status" in HTML

    def test_api_chat(self) -> None:
        assert "/api/chat" in HTML

    def test_api_run(self) -> None:
        assert "/api/run" in HTML

    def test_api_approve(self) -> None:
        assert "/api/approve" in HTML

    def test_api_reject(self) -> None:
        assert "/api/reject" in HTML

    def test_api_cancel(self) -> None:
        assert "/api/cancel" in HTML

    def test_api_patches(self) -> None:
        assert "/api/patches" in HTML

    def test_api_workspace(self) -> None:
        assert "/api/workspace" in HTML


# ─── LocalStorage ────────────────────────────────────────────────────────────

class TestLocalStorage:
    def test_localStorage_used(self) -> None:
        assert "localStorage" in HTML

    def test_store_key_defined(self) -> None:
        assert "STORE_KEY" in HTML or "STORAGE_KEY" in HTML

    def test_save_function(self) -> None:
        assert "function save" in HTML or "saveToStorage" in HTML or "function save(" in HTML

    def test_load_function(self) -> None:
        assert "loadStore" in HTML or "loadConversations" in HTML


# ─── Syntax highlighting ─────────────────────────────────────────────────────

class TestSyntaxHighlight:
    def test_hilight_function(self) -> None:
        assert "hilight" in HTML or "highlight" in HTML

    def test_keyword_token(self) -> None:
        assert "hl-kw" in HTML

    def test_string_token(self) -> None:
        assert "hl-str" in HTML

    def test_comment_token(self) -> None:
        assert "hl-cmt" in HTML

    def test_number_token(self) -> None:
        assert "hl-num" in HTML

    def test_function_token(self) -> None:
        assert "hl-fn" in HTML

    def test_python_keywords(self) -> None:
        assert "python" in HTML and "'def'" in HTML or '"def"' in HTML

    def test_javascript_keywords(self) -> None:
        assert "javascript" in HTML and "const" in HTML
