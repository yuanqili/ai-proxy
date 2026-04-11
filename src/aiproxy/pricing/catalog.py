"""Pull model pricing from an upstream catalog (LiteLLM).

LiteLLM maintains a large, community-updated JSON of model prices at

    https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json

Rather than manually curate our `pricing_seed.json` every time a new model
lands, we fetch this catalog on demand (via a button in the Pricing tab) and
use it to backfill missing rows + supersede outdated ones. The workflow is
two-phase:

  1. `fetch_catalog()` + `diff_against_db()` produce a preview summary:
     {"would_add": [...], "would_supersede": [...], "unchanged": N}
  2. If the user confirms, `apply_catalog()` writes the changes via
     `pricing_crud.upsert_current` — which creates a new effective row and
     closes the previous one, preserving the full versioned history.

LiteLLM's schema: keys are provider-qualified model IDs. We keep only the
entries whose `litellm_provider` matches one of our three providers:

  - openai:     bare keys like "gpt-4o-mini" (any `/` or `:` prefix is
                skipped — those are Azure / Bedrock / fine-tuning variants)
  - anthropic:  bare keys like "claude-sonnet-4-6"
  - openrouter: keys prefixed with "openrouter/" — we strip the prefix to
                recover the model ID the client actually sends in request
                bodies (e.g. "openrouter/anthropic/claude-3.5-sonnet" →
                "anthropic/claude-3.5-sonnet")

Per-token costs (LiteLLM uses `input_cost_per_token` / `output_cost_per_token`
/ `cache_read_input_token_cost`) are multiplied by 1_000_000 to match our
stored `_per_1m_usd` columns.
"""
from __future__ import annotations

import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from aiproxy.db.crud import pricing as pricing_crud

LITELLM_CATALOG_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Floating-point slop when deciding whether a pricing row is "unchanged".
# Prices are stored as per-million USD, so a single-cent epsilon is plenty.
_PRICE_EPS = 1e-6


def normalize_entry(key: str, val: dict) -> dict | None:
    """Convert one LiteLLM catalog entry into our pricing row shape.

    Returns None for entries that don't apply to us (non-chat modes,
    providers we don't proxy, Azure / Bedrock / fine-tuning variants, or
    entries missing a numeric token cost).
    """
    if not isinstance(val, dict):
        return None
    if val.get("mode") != "chat":
        return None
    prov = val.get("litellm_provider")
    if prov not in ("openai", "anthropic", "openrouter"):
        return None

    if prov == "openrouter":
        if not key.startswith("openrouter/"):
            return None
        model = key[len("openrouter/"):]
    else:
        # Bare model ID only — reject "azure/…", "bedrock/…", "ft:…" variants.
        if "/" in key or ":" in key:
            return None
        model = key

    i = val.get("input_cost_per_token")
    o = val.get("output_cost_per_token")
    if not isinstance(i, (int, float)) or not isinstance(o, (int, float)):
        return None

    cached_raw = val.get("cache_read_input_token_cost")
    cached_per_1m = (
        float(cached_raw) * 1_000_000 if isinstance(cached_raw, (int, float)) else None
    )

    return {
        "provider": prov,
        "model": model,
        "input_per_1m_usd": float(i) * 1_000_000,
        "output_per_1m_usd": float(o) * 1_000_000,
        "cached_per_1m_usd": cached_per_1m,
    }


def parse_catalog(data: dict) -> list[dict]:
    """Walk a parsed LiteLLM JSON blob and return the list of normalized
    entries that apply to our three providers."""
    if not isinstance(data, dict):
        return []
    entries: list[dict] = []
    for key, val in data.items():
        if key == "sample_spec":
            continue
        e = normalize_entry(key, val)
        if e is not None:
            entries.append(e)
    return entries


async def fetch_catalog(
    url: str = LITELLM_CATALOG_URL,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 30.0,
) -> list[dict]:
    """Fetch the LiteLLM catalog and return normalized entries.

    Pass `http_client` in tests to route the request through a respx mock;
    in production the default path opens a short-lived client.
    """
    if http_client is None:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
    else:
        r = await http_client.get(url)
        r.raise_for_status()
        data = r.json()
    return parse_catalog(data)


def _prices_equal(
    existing_input: float,
    existing_output: float,
    existing_cached: float | None,
    new_input: float,
    new_output: float,
    new_cached: float | None,
) -> bool:
    if abs(existing_input - new_input) > _PRICE_EPS:
        return False
    if abs(existing_output - new_output) > _PRICE_EPS:
        return False
    a = existing_cached if existing_cached is not None else -1.0
    b = new_cached if new_cached is not None else -1.0
    if abs(a - b) > _PRICE_EPS:
        return False
    return True


async def diff_against_db(
    session: AsyncSession,
    entries: list[dict],
) -> dict:
    """Compare `entries` against the pricing rows currently in effect.

    Returns a dict with three buckets:
      - would_add:       entries with no matching (provider, model) row
      - would_supersede: entries whose existing row has different prices
      - unchanged:       count of entries that already match exactly
    """
    now = time.time()
    would_add: list[dict] = []
    would_supersede: list[dict] = []
    unchanged = 0
    for e in entries:
        existing = await pricing_crud.find_effective(
            session, provider=e["provider"], model=e["model"], at=now,
        )
        if existing is None:
            would_add.append(e)
            continue
        if _prices_equal(
            existing.input_per_1m_usd, existing.output_per_1m_usd,
            existing.cached_per_1m_usd,
            e["input_per_1m_usd"], e["output_per_1m_usd"], e["cached_per_1m_usd"],
        ):
            unchanged += 1
            continue
        would_supersede.append({
            "pricing_id": existing.id,
            "provider": e["provider"],
            "model": e["model"],
            "old": {
                "input_per_1m_usd": existing.input_per_1m_usd,
                "output_per_1m_usd": existing.output_per_1m_usd,
                "cached_per_1m_usd": existing.cached_per_1m_usd,
            },
            "new": {
                "input_per_1m_usd": e["input_per_1m_usd"],
                "output_per_1m_usd": e["output_per_1m_usd"],
                "cached_per_1m_usd": e["cached_per_1m_usd"],
            },
        })
    return {
        "would_add": would_add,
        "would_supersede": would_supersede,
        "unchanged": unchanged,
    }


async def apply_catalog(
    session: AsyncSession,
    entries: list[dict],
) -> dict:
    """Write catalog entries into the pricing table.

    Re-runs the diff so it only touches rows that are actually new or
    changed — unchanged entries are skipped (otherwise `upsert_current`
    would uselessly close + reinsert an identical row on every refresh).

    Returns a summary with the counts of rows added and superseded.
    """
    diff = await diff_against_db(session, entries)
    for e in diff["would_add"]:
        await pricing_crud.upsert_current(
            session,
            provider=e["provider"],
            model=e["model"],
            input_per_1m_usd=e["input_per_1m_usd"],
            output_per_1m_usd=e["output_per_1m_usd"],
            cached_per_1m_usd=e["cached_per_1m_usd"],
            note="litellm-catalog",
        )
    for change in diff["would_supersede"]:
        new_prices = change["new"]
        await pricing_crud.upsert_current(
            session,
            provider=change["provider"],
            model=change["model"],
            input_per_1m_usd=new_prices["input_per_1m_usd"],
            output_per_1m_usd=new_prices["output_per_1m_usd"],
            cached_per_1m_usd=new_prices["cached_per_1m_usd"],
            note="litellm-catalog",
        )
    return {
        "added": len(diff["would_add"]),
        "superseded": len(diff["would_supersede"]),
        "unchanged": diff["unchanged"],
    }
