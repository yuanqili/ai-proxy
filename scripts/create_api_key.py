"""Create a new proxy API key and print it.

Usage:
    uv run python scripts/create_api_key.py --name laptop-dev
    uv run python scripts/create_api_key.py --name prod-app --note "backend server"
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from aiproxy.db.crud import api_keys as api_keys_crud
from aiproxy.db.engine import create_engine_and_sessionmaker
from aiproxy.db.models import Base
from aiproxy.settings import settings


def generate_key() -> str:
    """Generate a key like sk-aiprx-<32 random url-safe chars>."""
    return "sk-aiprx-" + secrets.token_urlsafe(24)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Human label for the key")
    parser.add_argument("--note", default=None, help="Optional note")
    args = parser.parse_args()

    engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    key = generate_key()
    async with sessionmaker() as session:
        await api_keys_crud.create(
            session,
            key=key,
            name=args.name,
            note=args.note,
            valid_from=None,
            valid_to=None,
        )
        await session.commit()
    await engine.dispose()

    print(f"Created API key '{args.name}':")
    print(f"  {key}")
    print()
    print("Use it as: Authorization: Bearer <key>")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
