"""Simple FastAPI server to generate verification codes for users."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from squid.api.dependencies import Accounts
from squid.api.errors import register_exception_handlers, responses
from squid.api.idempotency import IdempotencyResponseMiddleware, enforce_request_idempotency
from squid.api.rate_limit import RateLimitMiddleware, create_rate_limiter, enforce_route_rate_limits
from squid.api.security import Principal, Scope, require
from squid.api.v1 import TAGS_METADATA
from squid.api.v1 import router as v1_router
from squid.bootstrap import create_api_runtime
from squid.config import ApiProcessConfig, RuntimeConfig, load_api_process_config
from squid.logging_config import configure_api_logging
from squid.observability import configure_observability, instrument_api_app
from squid.runtime import ApiServices, ApplicationRuntime

RuntimeFactory = Callable[[RuntimeConfig], ApplicationRuntime[ApiServices]]
ConfigFactory = Callable[[], ApiProcessConfig]


router = APIRouter()


@router.get("/livez")
async def live() -> dict[str, str]:
    """Report only whether the API process can service requests."""
    return {"status": "ok"}


@router.get("/health")
@router.get("/readyz")
async def ready(request: Request, response: Response) -> dict[str, str]:
    """Report whether required database state matches this release."""
    try:
        await request.app.state.runtime.ready()
    except Exception:
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready"}


class User(BaseModel):
    """A user model."""

    uuid: UUID


@router.post(
    "/verify",
    status_code=201,
    responses=responses(401, 403, 404, 409, 422, 429, 503),
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
)
@router.post(
    "/v1/verify",
    status_code=201,
    responses=responses(401, 403, 404, 409, 422, 429, 503),
    tags=["users"],
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
)
async def get_verification_code(
    user: User,
    accounts: Accounts,
    _principal: Annotated[Principal, Depends(require(Scope.VERIFY))],
) -> int:
    """Generate a verification code for a user."""
    return await accounts.generate_verification_code(user.uuid)


def create_api_app(
    runtime_factory: RuntimeFactory = create_api_runtime,
    *,
    config: ApiProcessConfig | None = None,
    config_factory: ConfigFactory = load_api_process_config,
) -> FastAPI:
    """Create an API application with an explicitly owned runtime."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_config = config or config_factory()
        app.state.config = resolved_config
        limiter, policies = create_rate_limiter(resolved_config.rate_limit)
        app.state.rate_limiter = limiter
        app.state.rate_limit_policies = policies
        try:
            async with runtime_factory(resolved_config.runtime) as runtime:
                app.state.runtime = runtime
                yield
        finally:
            await limiter.aclose()

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
    api.add_middleware(RateLimitMiddleware)
    api.add_middleware(IdempotencyResponseMiddleware)
    resolved_for_middleware = config
    cors_origins = (
        resolved_for_middleware.api.cors_origins if isinstance(resolved_for_middleware, ApiProcessConfig) else ()
    )
    if cors_origins:
        api.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=[
                "Accept",
                "Accept-Language",
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "If-Match",
                "X-CSRF-Token",
                "X-Squid-Installation-ID",
                "X-Squid-Installation-Secret",
            ],
            expose_headers=["ETag", "RateLimit", "RateLimit-Policy", "Retry-After"],
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
            proxy_headers=bool(resolved_config.api.trusted_proxy_ips),
            forwarded_allow_ips=list(resolved_config.api.trusted_proxy_ips),
        )
    finally:
        observability.shutdown()


if __name__ == "__main__":
    main()
