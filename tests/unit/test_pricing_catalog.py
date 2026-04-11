"""Unit tests for pricing catalog normalization + diff logic."""
from aiproxy.pricing.catalog import normalize_entry, parse_catalog


def test_normalize_openai_bare_key() -> None:
    out = normalize_entry("gpt-4o-mini", {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 0.15e-6,
        "output_cost_per_token": 0.60e-6,
        "cache_read_input_token_cost": 0.075e-6,
    })
    assert out is not None
    assert out["provider"] == "openai"
    assert out["model"] == "gpt-4o-mini"
    assert out["input_per_1m_usd"] == 0.15
    assert out["output_per_1m_usd"] == 0.60
    assert out["cached_per_1m_usd"] == 0.075


def test_normalize_anthropic_sonnet_4_6() -> None:
    """The exact case the user's request was missing pricing for."""
    out = normalize_entry("claude-sonnet-4-6", {
        "litellm_provider": "anthropic",
        "mode": "chat",
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
        "cache_read_input_token_cost": 3e-7,
    })
    assert out == {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "input_per_1m_usd": 3.0,
        "output_per_1m_usd": 15.0,
        "cached_per_1m_usd": 0.3,
    }


def test_normalize_openrouter_strips_prefix() -> None:
    out = normalize_entry("openrouter/anthropic/claude-3.5-sonnet", {
        "litellm_provider": "openrouter",
        "mode": "chat",
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
    })
    assert out is not None
    assert out["provider"] == "openrouter"
    # Prefix stripped so it matches what clients send in body.model.
    assert out["model"] == "anthropic/claude-3.5-sonnet"
    assert out["cached_per_1m_usd"] is None


def test_normalize_skips_non_chat_modes() -> None:
    assert normalize_entry("text-embedding-3-small", {
        "litellm_provider": "openai",
        "mode": "embedding",
        "input_cost_per_token": 1e-8,
        "output_cost_per_token": 0,
    }) is None


def test_normalize_skips_other_providers() -> None:
    assert normalize_entry("claude-3-sonnet-bedrock", {
        "litellm_provider": "bedrock_converse",
        "mode": "chat",
        "input_cost_per_token": 3e-6,
        "output_cost_per_token": 15e-6,
    }) is None


def test_normalize_skips_prefixed_openai_variants() -> None:
    """Azure / Bedrock / fine-tuning variants have a / or : in the key;
    we only want bare provider keys."""
    assert normalize_entry("azure/gpt-4o", {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 1e-6,
    }) is None
    assert normalize_entry("ft:gpt-4o-2024-08-06:acme", {
        "litellm_provider": "openai",
        "mode": "chat",
        "input_cost_per_token": 1e-6,
        "output_cost_per_token": 1e-6,
    }) is None


def test_normalize_skips_missing_costs() -> None:
    assert normalize_entry("gpt-4o", {
        "litellm_provider": "openai",
        "mode": "chat",
        # input_cost_per_token absent
        "output_cost_per_token": 1e-6,
    }) is None


def test_parse_catalog_filters_sample_spec() -> None:
    data = {
        "sample_spec": {"input_cost_per_token": 0, "litellm_provider": "openai", "mode": "chat"},
        "gpt-4o-mini": {
            "litellm_provider": "openai", "mode": "chat",
            "input_cost_per_token": 1.5e-7, "output_cost_per_token": 6e-7,
        },
        "claude-sonnet-4-6": {
            "litellm_provider": "anthropic", "mode": "chat",
            "input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6,
        },
        "bedrock/anthropic.claude-3": {
            "litellm_provider": "bedrock_converse", "mode": "chat",
            "input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6,
        },
    }
    entries = parse_catalog(data)
    provs_models = {(e["provider"], e["model"]) for e in entries}
    assert provs_models == {
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-sonnet-4-6"),
    }
