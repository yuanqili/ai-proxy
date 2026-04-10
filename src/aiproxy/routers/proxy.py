"""Provider-dispatching proxy router."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from aiproxy.auth.proxy_auth import ApiKeyCache, verify_header
from aiproxy.core.passthrough import PassthroughEngine
from aiproxy.providers.base import Provider


def create_router(
    *,
    engine: PassthroughEngine,
    providers: dict[str, Provider],
    cache: ApiKeyCache,
) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/{provider}/{full_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    )
    async def dispatch(provider: str, full_path: str, request: Request):
        p = providers.get(provider)
        if p is None:
            raise HTTPException(status_code=404, detail=f"unknown provider: {provider}")

        # Auth
        auth_header = request.headers.get("authorization") or request.headers.get("x-api-key")
        result = await verify_header(auth_header, cache)
        if not result.ok:
            return JSONResponse(
                {"error": "unauthorized", "reason": result.reason},
                status_code=401,
            )

        # Gather client data
        client_headers = dict(request.headers)
        # Strip the proxy key so we don't store it
        client_headers.pop("authorization", None)
        client_headers.pop("x-api-key", None)
        client_headers.pop("cookie", None)

        client_body = await request.body()
        client_ip = request.client.host if request.client else None
        client_ua = request.headers.get("user-agent")
        req_id = uuid.uuid4().hex[:12]

        status_code, resp_headers, stream = await engine.forward(
            provider=p,
            client_path=full_path,
            req_id=req_id,
            method=request.method,
            client_headers=client_headers,
            client_query=list(request.query_params.multi_items()),
            client_body=client_body,
            client_ip=client_ip,
            client_ua=client_ua,
            api_key_id=result.api_key_id,
            started_at=time.time(),
        )

        return StreamingResponse(
            stream,
            status_code=status_code,
            headers=resp_headers,
        )

    return router
