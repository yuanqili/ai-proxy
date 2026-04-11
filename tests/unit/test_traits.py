"""Unit tests for core/traits.py — request body trait detection."""
import json

from aiproxy.core.traits import detect_request_traits


def _b(obj) -> bytes:
    return json.dumps(obj).encode()


# ── Empty / malformed input ────────────────────────────────────────────────

def test_empty_body_all_false() -> None:
    assert detect_request_traits(None) == {
        "request_has_image": False,
        "request_has_file": False,
        "response_is_json": False,
    }
    assert detect_request_traits(b"") == {
        "request_has_image": False,
        "request_has_file": False,
        "response_is_json": False,
    }


def test_invalid_json_all_false() -> None:
    out = detect_request_traits(b"<not json>")
    assert out["request_has_image"] is False
    assert out["request_has_file"] is False
    assert out["response_is_json"] is False


def test_non_dict_root_all_false() -> None:
    assert all(v is False for v in detect_request_traits(b'["a","b"]').values())
    assert all(v is False for v in detect_request_traits(b'42').values())


# ── Plain text request — no traits ─────────────────────────────────────────

def test_plain_string_content_no_traits() -> None:
    body = _b({"model": "gpt-4o", "messages": [
        {"role": "user", "content": "hi"},
    ]})
    out = detect_request_traits(body)
    assert out["request_has_image"] is False
    assert out["request_has_file"] is False


# ── Image detection ────────────────────────────────────────────────────────

def test_openai_image_url() -> None:
    body = _b({"messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "what's this?"},
            {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
        ]},
    ]})
    assert detect_request_traits(body)["request_has_image"] is True


def test_openai_input_image_responses_api() -> None:
    body = _b({"input": [
        {"role": "user", "content": [
            {"type": "input_image", "image_url": "https://x/y.png"},
        ]},
    ]})
    assert detect_request_traits(body)["request_has_image"] is True


def test_anthropic_image_block() -> None:
    body = _b({"model": "claude-sonnet-4-6", "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "ok"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "xx",
            }},
        ]},
    ]})
    assert detect_request_traits(body)["request_has_image"] is True


# ── File detection ─────────────────────────────────────────────────────────

def test_openai_file_part() -> None:
    body = _b({"messages": [
        {"role": "user", "content": [
            {"type": "file", "file": {"file_id": "file-abc"}},
        ]},
    ]})
    out = detect_request_traits(body)
    assert out["request_has_file"] is True
    assert out["request_has_image"] is False


def test_anthropic_document_part() -> None:
    body = _b({"messages": [
        {"role": "user", "content": [
            {"type": "document", "source": {"type": "base64",
             "media_type": "application/pdf", "data": "xx"}},
        ]},
    ]})
    assert detect_request_traits(body)["request_has_file"] is True


def test_image_and_file_in_same_request() -> None:
    body = _b({"messages": [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://x"}},
            {"type": "file", "file": {"file_id": "file-1"}},
        ]},
    ]})
    out = detect_request_traits(body)
    assert out["request_has_image"] is True
    assert out["request_has_file"] is True


# ── JSON response format ───────────────────────────────────────────────────

def test_response_format_json_object() -> None:
    body = _b({"messages": [{"role": "user", "content": "x"}],
               "response_format": {"type": "json_object"}})
    assert detect_request_traits(body)["response_is_json"] is True


def test_response_format_json_schema() -> None:
    body = _b({"messages": [{"role": "user", "content": "x"}],
               "response_format": {"type": "json_schema",
                                   "json_schema": {"name": "x", "schema": {}}}})
    assert detect_request_traits(body)["response_is_json"] is True


def test_response_format_text_not_json() -> None:
    body = _b({"messages": [{"role": "user", "content": "x"}],
               "response_format": {"type": "text"}})
    assert detect_request_traits(body)["response_is_json"] is False


def test_no_response_format_not_json() -> None:
    body = _b({"messages": [{"role": "user", "content": "x"}]})
    assert detect_request_traits(body)["response_is_json"] is False


# ── Tool use → also counts as "structured / JSON output" ──────────────────

def test_openai_tools_array_marks_json() -> None:
    body = _b({
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "x"}],
        "tools": [{"type": "function",
                   "function": {"name": "f", "parameters": {}}}],
    })
    assert detect_request_traits(body)["response_is_json"] is True


def test_anthropic_tools_array_marks_json() -> None:
    body = _b({
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "x"}],
        "tools": [{"name": "submit_taxonomy",
                   "description": "...",
                   "input_schema": {"type": "object", "properties": {}}}],
    })
    assert detect_request_traits(body)["response_is_json"] is True


def test_empty_tools_array_does_not_mark_json() -> None:
    body = _b({"messages": [{"role": "user", "content": "x"}], "tools": []})
    assert detect_request_traits(body)["response_is_json"] is False


def test_tools_not_a_list_does_not_crash() -> None:
    body = _b({"messages": [{"role": "user", "content": "x"}], "tools": "oops"})
    assert detect_request_traits(body)["response_is_json"] is False


# ── Defensive: weird shapes don't crash ────────────────────────────────────

def test_messages_not_a_list() -> None:
    body = _b({"messages": "oops"})
    assert detect_request_traits(body) == {
        "request_has_image": False, "request_has_file": False,
        "response_is_json": False,
    }


def test_message_not_dict() -> None:
    body = _b({"messages": ["nope", 1, None]})
    assert detect_request_traits(body)["request_has_image"] is False


def test_part_not_dict() -> None:
    body = _b({"messages": [
        {"role": "user", "content": ["text", 42, None]},
    ]})
    assert detect_request_traits(body)["request_has_image"] is False
