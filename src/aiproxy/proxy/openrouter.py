"""OpenRouter passthrough proxy with streaming tee to the dashboard bus.

The proxy:
- Accepts any request under /openrouter/...
- Rewrites the path to OpenRouter's /api/v1/...
- Injects the server-side OpenRouter API key
- Forwards headers (minus hop-by-hop), query, and body
- Streams the upstream response back to the client chunk-by-chunk
- For each chunk, ALSO publishes it to the StreamBus so dashboard subscribers
  can watch the generation in real time
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from aiproxy.bus import RequestMeta, bus, registry
from aiproxy.core.headers import clean_downstream_headers, clean_upstream_headers
from aiproxy.settings import settings

router = APIRouter()

UPSTREAM_PREFIX = "/api/v1"


def _extract_model(body_bytes: bytes) -> str | None:
    """Best-effort JSON parse to extract 'model' field for dashboard display."""
    if not body_bytes:
        return None
    try:
        obj = json.loads(body_bytes)
        if isinstance(obj, dict):
            m = obj.get("model")
            return m if isinstance(m, str) else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    return None


def _decode_chunk(chunk: bytes) -> str | None:
    """Try to decode a chunk as UTF-8 text for dashboard display.

    OpenAI-style SSE is text, so this usually succeeds. If it fails,
    we fall back to base64 on the dashboard side.
    """
    try:
        return chunk.decode("utf-8")
    except UnicodeDecodeError:
        return None


@router.api_route(
    "/openrouter/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def openrouter_passthrough(full_path: str, request: Request) -> Response:
    req_id = uuid.uuid4().hex[:12]

    # Build upstream URL
    upstream_url = f"{settings.openrouter_base_url}{UPSTREAM_PREFIX}/{full_path}"

    # Prepare headers — strip hop-by-hop, inject upstream auth
    headers = clean_upstream_headers(dict(request.headers))
    headers["authorization"] = f"Bearer {settings.openrouter_key}"

    # Read request body (needed to peek at model name, and to forward)
    body = await request.body()
    model = _extract_model(body)

    # Register the request so dashboard sees it immediately
    meta = RequestMeta(
        req_id=req_id,
        provider="openrouter",
        model=model,
        method=request.method,
        path=f"/{full_path}",
        client_ip=request.client.host if request.client else None,
    )
    registry.start(meta)

    client: httpx.AsyncClient = request.app.state.http_client

    # Build upstream request — must be sent with stream=True so we can
    # interleave forwarding with dashboard publishing.
    upstream_req = client.build_request(
        method=request.method,
        url=upstream_url,
        headers=headers,
        content=body if body else None,
        params=request.query_params.multi_items(),
    )

    try:
        upstream_resp = await client.send(upstream_req, stream=True)
    except httpx.HTTPError as e:
        registry.finish(req_id, status="error", error=f"upstream_connect: {e}")
        return Response(
            content=f'{{"error":"upstream connection failed: {e}"}}',
            status_code=502,
            media_type="application/json",
        )

    registry.update(
        req_id, status="streaming", status_code=upstream_resp.status_code
    )

    # The tee: each chunk goes to both the downstream client (via yield)
    # and the dashboard bus (via publish).
    async def body_iter() -> AsyncIterator[bytes]:
        chunk_idx = 0
        total_bytes = 0
        try:
            async for chunk in upstream_resp.aiter_raw():
                total_bytes += len(chunk)
                chunk_idx += 1

                # Publish to dashboard subscribers — only if someone's listening,
                # to avoid dict lookups for the common no-watcher case.
                if bus.has_subscribers(req_id):
                    text = _decode_chunk(chunk)
                    event = {
                        "type": "chunk",
                        "idx": chunk_idx,
                        "ts": time.time(),
                        "size": len(chunk),
                    }
                    if text is not None:
                        event["text"] = text
                    else:
                        event["b64"] = base64.b64encode(chunk).decode("ascii")
                    bus.publish(req_id, event)

                yield chunk

            # Normal completion
            registry.update(req_id, bytes_out=total_bytes, chunks=chunk_idx)
            if bus.has_subscribers(req_id):
                bus.publish(
                    req_id,
                    {"type": "done", "chunks": chunk_idx, "bytes": total_bytes},
                )
            registry.finish(
                req_id,
                status="done",
                bytes_out=total_bytes,
                chunks=chunk_idx,
            )
        except Exception as e:
            registry.finish(req_id, status="error", error=f"stream_error: {e}")
            if bus.has_subscribers(req_id):
                bus.publish(req_id, {"type": "error", "error": str(e)})
            raise
        finally:
            await upstream_resp.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream_resp.status_code,
        headers=clean_downstream_headers(dict(upstream_resp.headers)),
        media_type=upstream_resp.headers.get("content-type"),
    )
