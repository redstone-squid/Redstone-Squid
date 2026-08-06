"""Simple FastAPI server to generate verification codes for users."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from squid.api.dependencies import get_services
from squid.api.errors import register_exception_handlers, responses
from squid.api.security import Principal, Scope, require
from squid.api.v1 import TAGS_METADATA
from squid.api.v1 import router as v1_router
from squid.bootstrap import create_application_runtime
from squid.config import ApiProcessConfig, RuntimeConfig, load_api_process_config
from squid.logging_config import configure_api_logging
from squid.observability import configure_observability, instrument_api_app
from squid.runtime import ApplicationRuntime, ApplicationServices

RuntimeFactory = Callable[[RuntimeConfig], ApplicationRuntime]
ConfigFactory = Callable[[], ApiProcessConfig]


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class User(BaseModel):
    """A user model."""

    uuid: UUID


@router.post("/verify", status_code=201, responses=responses(401, 403, 404, 422, 503))
@router.post("/v1/verify", status_code=201, responses=responses(401, 403, 404, 422, 503), tags=["users"])
async def get_verification_code(
    user: User,
    services: Annotated[ApplicationServices, Depends(get_services)],
    _principal: Annotated[Principal, Depends(require(Scope.VERIFY))],
) -> int:
    """Generate a verification code for a user."""
    return await services.users.generate_verification_code(user.uuid)


def create_api_app(
    runtime_factory: RuntimeFactory = create_application_runtime,
    *,
    config: ApiProcessConfig | None = None,
    config_factory: ConfigFactory = load_api_process_config,
) -> FastAPI:
    """Create an API application with an explicitly owned runtime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_config = config or config_factory()
        app.state.config = resolved_config
        async with runtime_factory(resolved_config.runtime) as runtime:
            app.state.runtime = runtime
            yield

    api = FastAPI(
        title="Redstone Squid API",
        version="1.0.0",
        description="Versioned public API for the Redstone Squid build catalog.",
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
    )
    register_exception_handlers(api)
    api.include_router(router)
    api.include_router(v1_router)
    resolved_for_middleware = config
    cors_origins = (
        resolved_for_middleware.api.cors_origins if isinstance(resolved_for_middleware, ApiProcessConfig) else ()
    )
    if cors_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_methods=["GET"],
            allow_headers=["Accept", "Accept-Language", "Authorization", "Content-Type"],
        )
    if config is not None:
        instrument_api_app(api, config.observability)
    return api


app = create_api_app()


def main(process_config: ApiProcessConfig | None = None) -> None:
    """Run the FastAPI server."""
    import uvicorn

    resolved_config = process_config or load_api_process_config()
    configure_api_logging(resolved_config.logging)
    observability = configure_observability(resolved_config.observability, service_name="api")
    try:
        uvicorn.run(
            create_api_app(config=resolved_config),
            host="0.0.0.0",
            port=resolved_config.api.port,
            log_config=None,
        )
    finally:
        observability.shutdown()


if __name__ == "__main__":
    main()
