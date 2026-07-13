from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from memcore.api.routes_base import router as base_router
from memcore.api.routes_budget import router as budget_router
from memcore.api.routes_config import router as config_router
from memcore.api.routes_costs import router as costs_router
from memcore.api.routes_diagnostics import router as diagnostics_router
from memcore.api.routes_governance import router as governance_router
from memcore.api.routes_graph import router as graph_router
from memcore.api.routes_health import router as health_router
from memcore.api.routes_imports import router as imports_router
from memcore.api.routes_memory import router as memory_router
from memcore.api.routes_memory_control import router as memory_control_router
from memcore.api.routes_model_control import router as model_control_router
from memcore.api.routes_openwebui import router as openwebui_router
from memcore.api.routes_retrieval import router as retrieval_router
from memcore.api.routes_scheduler import router as scheduler_router
from memcore.config import get_settings
from memcore.imports.runtime import initialize_runtime_storage
from memcore.imports.worker import ImportReconstructionWorkerService
from memcore.observability.logging import configure_logging
from memcore.security.actor import MemoristActorMiddleware
from memcore.version import __version__


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker = ImportReconstructionWorkerService(settings)
    app.state.import_reconstruction_worker = worker
    worker.start()
    try:
        yield
    finally:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    initialize_runtime_storage(settings)

    app = FastAPI(
        title="memorist-core",
        version=__version__,
        docs_url="/memcore/docs",
        redoc_url=None,
        openapi_url="/memcore/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(MemoristActorMiddleware)
    app.include_router(health_router)
    app.include_router(config_router)
    app.include_router(costs_router)
    app.include_router(base_router)
    app.include_router(memory_router)
    app.include_router(memory_control_router)
    app.include_router(model_control_router)
    app.include_router(retrieval_router)
    app.include_router(governance_router)
    app.include_router(imports_router)
    app.include_router(openwebui_router)
    app.include_router(budget_router)
    app.include_router(diagnostics_router)
    app.include_router(scheduler_router)
    app.include_router(graph_router)

    @app.get("/openapi.json", include_in_schema=False)
    def openapi_alias() -> dict[str, object]:
        return app.openapi()

    return app


app = create_app()
