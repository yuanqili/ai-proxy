"""SQLAlchemy ORM models mirroring the spec §3 schema.

Only `api_keys` and `requests` are actively read/written in Phase 1. The
other tables (`chunks`, `pricing`, `config`) are created so migrations
stay in sync with the spec; their CRUD layers arrive in Phase 2.
"""
from __future__ import annotations

from sqlalchemy import (
    BLOB,
    REAL,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[float] = mapped_column(REAL, nullable=False)
    valid_from: Mapped[float | None] = mapped_column(REAL)
    valid_to: Mapped[float | None] = mapped_column(REAL)
    last_used_at: Mapped[float | None] = mapped_column(REAL)


class Request(Base):
    __tablename__ = "requests"

    req_id: Mapped[str] = mapped_column(Text, primary_key=True)
    api_key_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("api_keys.id"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text)
    is_streaming: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(Text)
    client_ua: Mapped[str | None] = mapped_column(Text)

    req_headers: Mapped[str] = mapped_column(Text, nullable=False)
    req_query: Mapped[str | None] = mapped_column(Text)
    req_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    req_body_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status_code: Mapped[int | None] = mapped_column(Integer)
    resp_headers: Mapped[str | None] = mapped_column(Text)
    resp_body: Mapped[bytes | None] = mapped_column(LargeBinary)
    resp_body_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[float] = mapped_column(REAL, nullable=False)
    upstream_sent_at: Mapped[float | None] = mapped_column(REAL)
    first_chunk_at: Mapped[float | None] = mapped_column(REAL)
    finished_at: Mapped[float | None] = mapped_column(REAL)

    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_class: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)

    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(REAL)
    pricing_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pricing.id"))

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    binaries_stripped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("idx_requests_started", "started_at"),
        Index("idx_requests_provider_model", "provider", "model"),
        Index("idx_requests_status", "status"),
        Index("idx_requests_api_key", "api_key_id"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    req_id: Mapped[str] = mapped_column(
        Text, ForeignKey("requests.req_id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    offset_ns: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(BLOB, nullable=False)

    __table_args__ = (Index("idx_chunks_req", "req_id"),)


class Pricing(Base):
    __tablename__ = "pricing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_per_1m_usd: Mapped[float] = mapped_column(REAL, nullable=False)
    output_per_1m_usd: Mapped[float] = mapped_column(REAL, nullable=False)
    cached_per_1m_usd: Mapped[float | None] = mapped_column(REAL)
    effective_from: Mapped[float] = mapped_column(REAL, nullable=False)
    effective_to: Mapped[float | None] = mapped_column(REAL)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(REAL, nullable=False)

    __table_args__ = (
        Index("idx_pricing_lookup", "provider", "model", "effective_from"),
    )


class Config(Base):
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(REAL, nullable=False)
