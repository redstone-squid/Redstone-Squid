"""Simple FastAPI server to generate verification codes for users."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from squid.api.contract import (
    ANONYMOUS,
    DEVICE,
    SERVICE,
    WEB_WRITE,
    compatibility_alias,
    contract,
    internal,
    transport_only,
)
from squid.api.dependencies import Accounts
from squid.api.errors import register_exception_handlers, responses
from squid.api.i18n import LocaleContextMiddleware
from squid.api.idempotency import IdempotencyResponseMiddleware, enforce_request_idempotency
from squid.api.openapi import install_openapi_contract
from squid.api.private_responses import PRIVATE_API_PATH_PREFIXES, PrivateResponseHeadersMiddleware
from squid.api.rate_limit import RateLimitMiddleware, create_rate_limiter, enforce_route_rate_limits
from squid.api.request_body import BoundedRequestBodyMiddleware
from squid.api.request_context import RequestContextMiddleware
from squid.api.security import Caller, requires
from squid.api.v1 import TAGS_METADATA
from squid.api.v1 import router as v1_router
from squid.bootstrap import create_api_runtime
from squid.config import ApiProcessConfig, RuntimeConfig, load_api_process_config, load_or_exit
from squid.logging_config import configure_api_logging
from squid.observability import configure_observability, instrument_api_app
from squid.permissions.domain.catalogue import ACCOUNT_VERIFY_RELAY
from squid.runtime import (
    ApiServices,
    ApplicationRuntime,
    BackgroundTaskSupervisor,
    start_log_capture,
    start_permission_epoch_watch,
)

RuntimeFactory = Callable[[RuntimeConfig], ApplicationRuntime[ApiServices]]
ConfigFactory = Callable[[], ApiProcessConfig]


router = APIRouter()


@router.get(
    "/livez",
    operation_id="health_live",
    openapi_extra=contract(security=[ANONYMOUS], cli=internal("Process liveness probe.")),
)
async def live() -> dict[str, str]:
    """Report only whether the API process can service requests."""
    return {"status": "ok"}


@router.get(
    "/health",
    operation_id="health_ready_compatibility",
    openapi_extra=contract(security=[ANONYMOUS], cli=compatibility_alias("health_ready")),
)
@router.get(
    "/readyz",
    operation_id="health_ready",
    openapi_extra=contract(security=[ANONYMOUS], cli=internal("Deployment readiness probe.")),
)
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
    operation_id="verification_create_compatibility",
    openapi_extra=contract(
        security=[SERVICE, WEB_WRITE, DEVICE],
        cli=compatibility_alias("verification_create"),
        scopes=("account.verify.relay",),
    ),
)
@router.post(
    "/v1/verify",
    status_code=201,
    responses=responses(401, 403, 404, 409, 422, 429, 503),
    tags=["users"],
    dependencies=[Depends(enforce_route_rate_limits), Depends(enforce_request_idempotency)],
    operation_id="verification_create",
    openapi_extra=contract(
        security=[SERVICE, WEB_WRITE, DEVICE],
        cli=transport_only(),
        scopes=("account.verify.relay",),
    ),
)
async def get_verification_code(
    user: User,
    accounts: Accounts,
    _caller: Annotated[Caller, Depends(requires(ACCOUNT_VERIFY_RELAY))],
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
                # The API holds its own rule cache, so it needs its own watcher:
                # a grant made in Discord has to reach HTTP checks too.
                supervisor = BackgroundTaskSupervisor()
                supervisor.capture_failures_into(runtime.services.error_reports)
                async with supervisor.running():
                    app.state.background_tasks = supervisor
                    start_permission_epoch_watch(supervisor, runtime.services.permission_epoch)
                    start_log_capture(
                        supervisor,
                        runtime.services.error_reports,
                        enabled=resolved_config.diagnostics.capture_logged_errors,
                        capacity=resolved_config.diagnostics.log_capture_queue,
                    )
                    yield
        finally:
            await limiter.aclose()

    api = FastAPI(
        title="Redstone Squid API",
        version="1.0.0",
        description=(
            "Versioned public API for the Redstone Squid build catalog. Every response carries a "
            "Request-Id header for correlation; send Request-Id or a W3C traceparent to have it "
            "propagated."
        ),
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
    )
    register_exception_handlers(api)
    api.include_router(router)
    api.include_router(v1_router)
    install_openapi_contract(api)
    api.add_middleware(RateLimitMiddleware)
    api.add_middleware(IdempotencyResponseMiddleware)
    api.add_middleware(BoundedRequestBodyMiddleware)
    api.add_middleware(PrivateResponseHeadersMiddleware, path_prefixes=PRIVATE_API_PATH_PREFIXES)
    # Added last of the unconditional stack so it is outermost: it stamps Request-Id onto rate-limit
    # rejections and idempotency replays alike, and its binding is visible to every inner layer.
    api.add_middleware(RequestContextMiddleware)
    api.add_middleware(LocaleContextMiddleware)
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
                "CSRF-Token",
                "Squid-Installation-ID",
                "Squid-Installation-Secret",
            ],
            expose_headers=["ETag", "RateLimit", "RateLimit-Policy", "Request-Id", "Retry-After"],
        )
    if config is not None:
        instrument_api_app(api, config.observability)
    return api


app = create_api_app()


def main(process_config: ApiProcessConfig | None = None) -> None:
    """Run the FastAPI server."""
    import uvicorn

    resolved_config = process_config or load_or_exit(load_api_process_config)
    configure_api_logging(resolved_config.logging, dev_mode=resolved_config.development_mode)
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
