"""Shared passthrough engine used by the provider dispatcher router.

Phase 1 responsibilities:
  - Build upstream request from client request (strip hop-by-hop headers,
    inject provider auth).
  - Stream the upstream response back to the client, yielding each chunk.
  - Persist request metadata at start ('pending') and finish ('done' or 'error').

Phase 1 does NOT:
  - Tee chunks to a dashboard bus.
  - Parse SSE / usage / cost.
  - Persist individual chunks.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers
from aiproxy.db.crud import requests as req_crud
from aiproxy.providers.base import Provider


class PassthroughEngine:
    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        sessionmaker: async_sessionmaker,
    ) -> None:
        self._client = http_client
        self._sessionmaker = sessionmaker

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

        # Persist the pending record
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

        upstream_url = f"{provider.base_url}{provider.map_path(client_path)}"
        upstream_headers = clean_upstream_headers(client_headers)
        upstream_headers = provider.inject_auth(upstream_headers)

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
            raise

        async def stream_and_persist() -> AsyncIterator[bytes]:
            buffer: list[bytes] = []
            try:
                async for chunk in upstream_resp.aiter_raw():
                    buffer.append(chunk)
                    yield chunk
                body_bytes = b"".join(buffer)
                async with self._sessionmaker() as session:
                    await req_crud.mark_finished(
                        session,
                        req_id=req_id,
                        status="done",
                        status_code=upstream_resp.status_code,
                        resp_headers=dict(upstream_resp.headers),
                        resp_body=body_bytes,
                        finished_at=time.time(),
                    )
                    await session.commit()
            except Exception as e:
                async with self._sessionmaker() as session:
                    await req_crud.mark_error(
                        session,
                        req_id=req_id,
                        error_class="stream_interrupted",
                        error_message=str(e),
                        finished_at=time.time(),
                    )
                    await session.commit()
                raise
            finally:
                await upstream_resp.aclose()

        return (
            upstream_resp.status_code,
            clean_downstream_headers(dict(upstream_resp.headers)),
            stream_and_persist(),
        )
