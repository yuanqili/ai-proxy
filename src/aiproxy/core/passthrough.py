"""Shared passthrough engine used by the provider dispatcher router.

Phase 2 responsibilities (additions over Phase 1):
  - Publish each chunk to the StreamBus (dashboard live tee)
  - Buffer chunks as (seq, offset_ns, data) tuples in memory
  - On finalize: batch-insert chunks, parse provider-specific usage,
    compute cost via the versioned pricing table, snapshot into requests row
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.bus import StreamBus
from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers
from aiproxy.db.crud import chunks as chunks_crud
from aiproxy.db.crud import requests as req_crud
from aiproxy.db.models import Request
from aiproxy.pricing.compute import compute_cost
from aiproxy.providers.base import Provider
from aiproxy.registry import RequestMeta, RequestRegistry


async def _finalize(
    *,
    sessionmaker: async_sessionmaker,
    registry: RequestRegistry,
    provider: Provider,
    req_id: str,
    final_status: str,
    status_code: int,
    resp_headers: dict[str, str],
    resp_body: bytes,
    error_class: str | None,
    error_message: str | None,
    chunk_buffer: list[tuple[int, int, bytes]],
    is_streaming: bool,
) -> None:
    """Persist terminal state + chunks + usage/cost for a request.

    Called from the stream generator's `finally` block under `asyncio.shield`
    so the write completes even if the generator is cancelled by a client
    disconnect.
    """
    now = time.time()
    async with sessionmaker() as session:
        if final_status == "error":
            await req_crud.mark_error(
                session,
                req_id=req_id,
                error_class=error_class or "unknown",
                error_message=error_message or "",
                finished_at=now,
            )
        else:
            # 'done' or 'canceled' — both use mark_finished with the respective status
            await req_crud.mark_finished(
                session,
                req_id=req_id,
                status=final_status,
                status_code=status_code,
                resp_headers=resp_headers,
                resp_body=resp_body,
                finished_at=now,
            )

        # Batch-insert chunks (only for streaming requests)
        if is_streaming and chunk_buffer:
            await chunks_crud.insert_batch(session, req_id, chunk_buffer)

        # Parse usage + cost, snapshot into requests row
        usage = provider.parse_usage(
            is_streaming=is_streaming,
            resp_body=resp_body if not is_streaming else None,
            chunks=[data for (_, _, data) in chunk_buffer] if is_streaming else None,
        )
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        cached_tokens = usage.cached_tokens if usage else None
        reasoning_tokens = usage.reasoning_tokens if usage else None

        pricing_id: int | None = None
        cost_usd: float | None = None
        if usage is not None and final_status != "error":
            # Get the model from the already-persisted request row
            result = await session.execute(
                select(Request).where(Request.req_id == req_id)
            )
            req_row = result.scalar_one_or_none()
            model = req_row.model if req_row else None
            if model:
                cost_result = await compute_cost(
                    session, provider=provider.name, model=model, usage=usage,
                )
                if cost_result:
                    pricing_id, cost_usd = cost_result

        await session.execute(
            update(Request)
            .where(Request.req_id == req_id)
            .values(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                reasoning_tokens=reasoning_tokens,
                cost_usd=cost_usd,
                pricing_id=pricing_id,
                chunk_count=len(chunk_buffer) if is_streaming else 0,
            )
        )
        await session.commit()

    # Update the in-memory registry
    registry.finish(
        req_id,
        status=final_status,
        status_code=status_code,
        error_class=error_class,
        error_message=error_message,
        chunks=len(chunk_buffer) if is_streaming else 0,
        bytes_out=sum(len(c) for (_, _, c) in chunk_buffer) if is_streaming else len(resp_body),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
    )


class PassthroughEngine:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        sessionmaker: async_sessionmaker,
        bus: StreamBus,
        registry: RequestRegistry,
    ) -> None:
        self._client = http_client
        self._sessionmaker = sessionmaker
        self._bus = bus
        self._registry = registry

    async def forward(
        self,
        *,
        provider: Provider,
        client_path: str,
        req_id: str,
        method: str,
        client_headers: dict[str, str],
        client_query: list[tuple[str, str]],
        client_body: bytes,
        client_ip: str | None,
        client_ua: str | None,
        api_key_id: int | None,
        started_at: float,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        """Forward a client request to the upstream and return (status, headers, stream).

        The caller (FastAPI route) wraps the returned stream in a StreamingResponse.
        All persistence happens inside this method.
        """
        model = provider.extract_model(client_body)
        is_streaming = provider.is_streaming_request(client_body, client_headers)

        # Persist the pending DB record
        async with self._sessionmaker() as session:
            await req_crud.create_pending(
                session,
                req_id=req_id,
                api_key_id=api_key_id,
                provider=provider.name,
                endpoint="/" + client_path.lstrip("/"),
                method=method,
                model=model,
                is_streaming=is_streaming,
                client_ip=client_ip,
                client_ua=client_ua,
                req_headers=client_headers,
                req_query=client_query or None,
                req_body=client_body,
                started_at=started_at,
            )
            await session.commit()

        # Register in the in-memory tracker
        self._registry.start(RequestMeta(
            req_id=req_id,
            provider=provider.name,
            model=model,
            method=method,
            path="/" + client_path.lstrip("/"),
            client_ip=client_ip,
            started_at=started_at,
        ))

        upstream_url = f"{provider.base_url}{provider.map_path(client_path)}"
        upstream_headers = clean_upstream_headers(client_headers)
        upstream_headers = provider.inject_auth(upstream_headers)
        # Force uncompressed responses so _finalize can parse usage from the body.
        # httpx would otherwise set Accept-Encoding: gzip,deflate automatically,
        # and our response buffer would be opaque compressed bytes.
        upstream_headers["accept-encoding"] = "identity"

        upstream_req = self._client.build_request(
            method=method,
            url=upstream_url,
            headers=upstream_headers,
            content=client_body if client_body else None,
            params=client_query,
        )

        try:
            upstream_resp = await self._client.send(upstream_req, stream=True)
        except httpx.ConnectError as e:
            async with self._sessionmaker() as session:
                await req_crud.mark_error(
                    session,
                    req_id=req_id,
                    error_class="upstream_connect",
                    error_message=str(e),
                    finished_at=time.time(),
                )
                await session.commit()
            self._registry.finish(
                req_id, status="error", error_class="upstream_connect", error_message=str(e)
            )
            raise
        except httpx.ReadTimeout as e:
            async with self._sessionmaker() as session:
                await req_crud.mark_error(
                    session,
                    req_id=req_id,
                    error_class="upstream_timeout",
                    error_message=str(e),
                    finished_at=time.time(),
                )
                await session.commit()
            self._registry.finish(
                req_id, status="error", error_class="upstream_timeout", error_message=str(e)
            )
            raise

        self._registry.update(req_id, status="streaming", status_code=upstream_resp.status_code)

        bus = self._bus
        sessionmaker = self._sessionmaker
        registry = self._registry

        async def stream_and_persist() -> AsyncIterator[bytes]:
            chunk_buffer: list[tuple[int, int, bytes]] = []
            first_ns: int | None = None
            final_status: str = "done"
            error_class: str | None = None
            error_message: str | None = None
            try:
                async for chunk in upstream_resp.aiter_raw():
                    ts_ns = time.monotonic_ns()
                    if first_ns is None:
                        first_ns = ts_ns
                    offset_ns = ts_ns - first_ns
                    seq = len(chunk_buffer)
                    chunk_buffer.append((seq, offset_ns, chunk))

                    # Publish to dashboard subscribers (short-circuit if no one listening)
                    if bus.has_subscribers(req_id):
                        bus.publish(req_id, {
                            "type": "chunk",
                            "req_id": req_id,
                            "seq": seq,
                            "offset_ns": offset_ns,
                            "size": len(chunk),
                        })

                    yield chunk
            except asyncio.CancelledError:
                # Client disconnected before the upstream stream finished.
                # Whatever we've buffered is what we got — persist as canceled.
                final_status = "canceled"
                raise
            except httpx.ReadError as e:
                final_status = "error"
                error_class = "stream_interrupted"
                error_message = str(e)
                raise
            except Exception as e:
                final_status = "error"
                error_class = "stream_interrupted"
                error_message = str(e)
                raise
            finally:
                # Always persist final state, even on cancellation/error.
                # Shield from outer cancellation so the DB write can complete.
                body_bytes = b"".join(data for (_, _, data) in chunk_buffer)
                try:
                    await asyncio.shield(_finalize(
                        sessionmaker=sessionmaker,
                        registry=registry,
                        provider=provider,
                        req_id=req_id,
                        final_status=final_status,
                        status_code=upstream_resp.status_code,
                        resp_headers=dict(upstream_resp.headers),
                        resp_body=body_bytes,
                        error_class=error_class,
                        error_message=error_message,
                        chunk_buffer=chunk_buffer,
                        is_streaming=is_streaming,
                    ))
                    # Publish a final 'done' or 'error' event to the chunk channel
                    if bus.has_subscribers(req_id):
                        bus.publish(req_id, {
                            "type": "done" if final_status != "error" else "error",
                            "req_id": req_id,
                            "status": final_status,
                        })
                finally:
                    await upstream_resp.aclose()

        return (
            upstream_resp.status_code,
            clean_downstream_headers(dict(upstream_resp.headers)),
            stream_and_persist(),
        )
