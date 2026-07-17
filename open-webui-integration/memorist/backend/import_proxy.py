"""Authenticated administrator proxy for the browser import workflow.

The Core import API is intentionally not exposed through the Open WebUI origin.
This router forwards only the operations used by the shipped Import surface,
binds every request to the verified Open WebUI administrator, and forces the
server-configured workspace on uploads.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ..shared.client import MemoristClient
from ..shared.errors import sanitize_error
from .router import AuthenticatedAdmin, OpenWebUIActor

router = APIRouter(prefix="/api/v1/memorist/imports", tags=["memorist-import-proxy"])


def _run_path(run_uuid: str, suffix: str = "") -> str:
    return f"/memcore/imports/{quote(run_uuid, safe='')}{suffix}"


def _safe_failure(response: httpx.Response) -> HTTPException:
    detail: Any = f"Memorist import request failed ({response.status_code})"
    try:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("detail") is not None:
            detail = payload["detail"]
    except Exception:
        pass
    return HTTPException(
        status_code=response.status_code,
        detail=sanitize_error(str(detail)),
    )


async def _forward(
    actor: OpenWebUIActor,
    method: str,
    core_path: str,
    request: Request | None = None,
) -> dict[str, Any]:
    client = MemoristClient()
    headers = client._actor_headers(
        method,
        core_path,
        actor.user_uuid,
        actor.workspace_uuid,
    )
    content: bytes | None = None
    if request is not None:
        content = await request.body()
        content_type = request.headers.get("content-type")
        if content_type:
            headers["Content-Type"] = content_type
    try:
        async with httpx.AsyncClient(
            timeout=client.config.timeout_seconds
        ) as transport:
            response = await transport.request(
                method,
                client.base_url + core_path,
                params=(
                    list(request.query_params.multi_items())
                    if request is not None
                    else None
                ),
                headers=headers,
                content=content,
            )
    except (httpx.HTTPError, OSError) as error:
        raise HTTPException(status_code=502, detail=sanitize_error(error)) from error
    if not response.is_success:
        raise _safe_failure(response)
    payload = response.json() if response.content else {}
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="Memorist Core returned an invalid import response",
        )
    return payload


@router.get("")
@router.get("/")
async def list_imports(
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "GET", "/memcore/imports", request)


@router.post("/upload-file")
async def upload_import_file(
    actor: AuthenticatedAdmin,
    file: UploadFile = File(...),
    mode: str = Form("inspect"),
    processing_mode: str | None = Form(None),
) -> dict[str, Any]:
    client = MemoristClient()
    core_path = "/memcore/imports/upload-file"
    headers = client._actor_headers(
        "POST",
        core_path,
        actor.user_uuid,
        actor.workspace_uuid,
    )
    data = {
        "mode": mode,
        "target_workspace_uuid": actor.workspace_uuid,
    }
    if processing_mode:
        data["processing_mode"] = processing_mode
    try:
        await file.seek(0)
        async with httpx.AsyncClient(
            timeout=client.config.timeout_seconds
        ) as transport:
            response = await transport.post(
                client.base_url + core_path,
                headers=headers,
                data=data,
                files={
                    "file": (
                        file.filename or "import.bin",
                        file.file,
                        file.content_type or "application/octet-stream",
                    )
                },
            )
    except (httpx.HTTPError, OSError) as error:
        raise HTTPException(status_code=502, detail=sanitize_error(error)) from error
    if not response.is_success:
        raise _safe_failure(response)
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=502,
            detail="Memorist Core returned an invalid upload response",
        )
    return payload


@router.get("/{run_uuid}")
async def get_import(run_uuid: str, actor: AuthenticatedAdmin) -> dict[str, Any]:
    return await _forward(actor, "GET", _run_path(run_uuid))


@router.post("/{run_uuid}/inspect")
async def inspect_import(
    run_uuid: str,
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "POST", _run_path(run_uuid, "/inspect"), request)


@router.post("/{run_uuid}/reconstruct")
async def reconstruct_import(
    run_uuid: str,
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "POST", _run_path(run_uuid, "/reconstruct"), request)


@router.post("/{run_uuid}/dry-run")
async def dry_run_import(
    run_uuid: str,
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "POST", _run_path(run_uuid, "/dry-run"), request)


@router.get("/{run_uuid}/dry-run-report")
async def dry_run_report(
    run_uuid: str,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "GET", _run_path(run_uuid, "/dry-run-report"))


@router.post("/{run_uuid}/commit")
async def commit_import(
    run_uuid: str,
    request: Request,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "POST", _run_path(run_uuid, "/commit"), request)


@router.get("/{run_uuid}/progress")
async def import_progress(
    run_uuid: str,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "GET", _run_path(run_uuid, "/progress"))


@router.get("/{run_uuid}/processing-report")
async def processing_report(
    run_uuid: str,
    actor: AuthenticatedAdmin,
) -> dict[str, Any]:
    return await _forward(actor, "GET", _run_path(run_uuid, "/processing-report"))


def _action_route(action: str):
    async def endpoint(
        run_uuid: str,
        request: Request,
        actor: AuthenticatedAdmin,
    ) -> dict[str, Any]:
        return await _forward(
            actor,
            "POST",
            _run_path(run_uuid, f"/{action}"),
            request,
        )

    return endpoint


for _action in ("pause", "resume", "cancel", "retry-failed"):
    router.add_api_route(
        f"/{{run_uuid}}/{_action}",
        _action_route(_action),
        methods=["POST"],
        name=f"memorist_import_{_action.replace('-', '_')}",
    )
