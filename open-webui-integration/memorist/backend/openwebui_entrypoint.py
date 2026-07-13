from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import Depends

from .router import OpenWebUIActor, require_openwebui_actor, router


def create_app() -> Any:
    """Mount Memorist into the pinned Open WebUI backend with native session auth."""
    from open_webui.main import app
    from open_webui.utils.auth import get_verified_user

    workspace_uuid = str(UUID(os.environ["MEMORIST_OPENWEBUI_WORKSPACE_UUID"]))
    verified_user_dependency = Depends(get_verified_user)

    def verified_memorist_actor(user: Any = verified_user_dependency) -> OpenWebUIActor:
        return OpenWebUIActor(user_uuid=str(user.id), workspace_uuid=workspace_uuid)

    app.dependency_overrides[require_openwebui_actor] = verified_memorist_actor
    if not any(route.path.startswith("/api/v1/memorist") for route in app.routes):
        app.include_router(router)
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "memorist.backend.openwebui_entrypoint:create_app",
        factory=True,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        workers=int(os.getenv("WEB_CONCURRENCY", "1")),
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )


if __name__ == "__main__":
    main()
