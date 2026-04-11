"""Detect coarse-grained "traits" of a chat-style request body.

These flags answer at-a-glance dashboard questions like
"does this request include an image?" or "is the model being asked for
JSON output?". They're computed once at request-creation time and stored
on the row, so the list endpoint can return them without re-scanning the
body for every page load.

The detection is intentionally permissive: we walk the JSON shape that
OpenAI / Anthropic / OpenRouter chat endpoints all share (`messages[]`
with mixed string-or-array content). On any parse error all flags are
False — never raise from this module.

Bumping ``DETECTOR_VERSION`` triggers a one-time rescan on the next
startup (see ``backfill_request_traits`` and the lifespan hook in app.py).
Bump it whenever you add or change a detection rule.
"""
from __future__ import annotations

import json
from typing import Any

# Bump on every detection-rule change. The lifespan in app.py compares this
# against the value in the `config` table; if they differ, all rows have
# their trait flags reset to NULL and the backfill walks every row again.
DETECTOR_VERSION = 2

# Content-part `type` values that imply an attached image, across the three
# providers we proxy. OpenAI uses `image_url` (chat completions) and
# `input_image` (responses API); Anthropic uses `image` in messages.
_IMAGE_TYPES: frozenset[str] = frozenset({"image", "image_url", "input_image"})

# Content-part `type` values that imply an attached file/document.
# OpenAI: `file` / `input_file`. Anthropic: `document` (PDFs).
_FILE_TYPES: frozenset[str] = frozenset({"file", "input_file", "document"})

# response_format.type values OpenAI / OpenRouter accept for JSON output.
_JSON_RESPONSE_FORMATS: frozenset[str] = frozenset({"json_object", "json_schema"})


def detect_request_traits(req_body: bytes | None) -> dict[str, bool]:
    """Inspect a chat-style request body and return trait flags.

    Returns a dict with keys ``request_has_image``, ``request_has_file``,
    and ``response_is_json``. All False if the body is empty, isn't JSON,
    or doesn't match any of the expected shapes — this is a best-effort
    classifier, not a validator.

    "JSON response" means the request is structured to elicit machine-readable
    output: either an explicit ``response_format`` of json_object/json_schema
    (OpenAI/OpenRouter) or a non-empty ``tools`` array (OpenAI/Anthropic
    function-/tool-calling, which always produces JSON arguments).
    """
    flags = {
        "request_has_image": False,
        "request_has_file": False,
        "response_is_json": False,
    }
    if not req_body:
        return flags
    try:
        obj = json.loads(req_body)
    except (ValueError, UnicodeDecodeError):
        return flags
    if not isinstance(obj, dict):
        return flags

    # Explicit "give me JSON" — OpenAI / OpenRouter response_format.
    rf = obj.get("response_format")
    if isinstance(rf, dict) and rf.get("type") in _JSON_RESPONSE_FORMATS:
        flags["response_is_json"] = True

    # Tool-calling (OpenAI tools / Anthropic tools) — the model's reply
    # contains structured JSON arguments, which is what the dashboard cares
    # about when filtering "structured output" requests.
    tools = obj.get("tools")
    if isinstance(tools, list) and tools:
        flags["response_is_json"] = True

    _scan_messages(obj.get("messages"), flags)
    # OpenAI responses API uses `input` instead of `messages`.
    if not flags["request_has_image"] and not flags["request_has_file"]:
        _scan_messages(obj.get("input"), flags)

    return flags


def _scan_messages(messages: Any, flags: dict[str, bool]) -> None:
    """Walk a messages-shaped list and set image/file flags in place."""
    if not isinstance(messages, list):
        return
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        # OpenAI/Anthropic both allow content to be either a plain string
        # (text only — no traits to detect) or an array of typed parts.
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in _IMAGE_TYPES:
                flags["request_has_image"] = True
            elif ptype in _FILE_TYPES:
                flags["request_has_file"] = True
            if flags["request_has_image"] and flags["request_has_file"]:
                return  # short-circuit; both flags found
