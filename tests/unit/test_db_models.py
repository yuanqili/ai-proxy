"""Tests for aiproxy.db.models — ensure all tables are declared and schema is creatable."""
import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from aiproxy.db.models import Base


EXPECTED_TABLES = {
    "requests",
    "chunks",
    "api_keys",
    "pricing",
    "config",
}


@pytest.mark.asyncio
async def test_all_tables_created() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def list_tables(sync_conn):  # type: ignore[no-untyped-def]
            return set(inspect(sync_conn).get_table_names())

        names = await conn.run_sync(list_tables)

    await engine.dispose()
    assert EXPECTED_TABLES.issubset(names), f"missing tables: {EXPECTED_TABLES - names}"


@pytest.mark.asyncio
async def test_requests_columns_present() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def inspect_cols(sync_conn):  # type: ignore[no-untyped-def]
            return {c["name"] for c in inspect(sync_conn).get_columns("requests")}

        cols = await conn.run_sync(inspect_cols)
    await engine.dispose()

    required = {
        "req_id", "api_key_id", "provider", "endpoint", "method", "model",
        "is_streaming", "client_ip", "client_ua",
        "req_headers", "req_query", "req_body", "req_body_size",
        "status_code", "resp_headers", "resp_body", "resp_body_size",
        "started_at", "upstream_sent_at", "first_chunk_at", "finished_at",
        "status", "error_class", "error_message",
        "input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens",
        "cost_usd", "pricing_id",
        "chunk_count", "binaries_stripped",
    }
    missing = required - cols
    assert not missing, f"missing columns in requests: {missing}"
