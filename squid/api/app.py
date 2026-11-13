"""HTTP API for the build catalogue, submissions, accounts, and moderation."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from uuid import UUID

from litestar import Litestar, Request, Response, get, post
from litestar.config.cors import CORSConfig
from litestar.di import NamedDependency, Provide
from litestar.middleware import DefineMiddleware
from litestar.openapi import OpenAPIConfig
from litestar.openapi.spec import Tag
from litestar.params import JSONBody
from pydantic import BaseModel
lazy import uvicorn

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
from squid.api.dependencies import Accounts, service_dependencies
from squid.api.errors import capture_exception, exception_handlers, responses
from squid.api.i18n import LocaleContextMiddleware
from squid.api.idempotency import (
    CompleteIdempotentResponseMiddleware,
    assert_idempotency_completion_installed,
    reserve_idempotent_before_request,
)
from squid.api.openapi import (
    ApplicationValueSchemaPlugin,
    ContractJsonRenderPlugin,
    ContractSwaggerRenderPlugin,
    install_openapi_contract,
)
from squid.api.private_responses import PRIVATE_API_PATH_PREFIXES, PrivateResponseHeadersMiddleware
from squid.api.rate_limit import RateLimitMiddleware, create_rate_limiter
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


@get(
    "/livez",
    operation_id="health_live",
    opt=contract(security=[ANONYMOUS], cli=internal("Process liveness probe.")),
)
async def live() -> dict[str, str]:
    """Report only whether the API process can service requests."""
    return {"status": "ok"}


@get(
    "/readyz",
    operation_id="health_ready",
    opt=contract(security=[ANONYMOUS], cli=internal("Deployment readiness probe.")),
)
async def ready(request: Request) -> Response[dict[str, str]]:
    """Report whether required database state matches this release."""
    try:
        await request.app.state.runtime.ready()
    except Exception:
        return Response({"status": "not_ready"}, status_code=503)
    return Response({"status": "ready"})


@get(
    "/health",
    operation_id="health_ready_compatibility",
    opt=contract(security=[ANONYMOUS], cli=compatibility_alias("health_ready")),
)
async def ready_compatibility(request: Request) -> Response[dict[str, str]]:
    return await ready.fn(request)


class User(BaseModel):
    """Minecraft Java UUID to associate with a verification code."""

    uuid: UUID


@post(
    "/v1/verify",
    status_code=201,
    responses=responses(401, 403, 404, 409, 422, 429, 503),
    dependencies={"_caller": Provide(requires(ACCOUNT_VERIFY_RELAY))},
    before_request=reserve_idempotent_before_request,
    operation_id="verification_create",
    tags=["users"],
    opt=contract(security=[SERVICE, WEB_WRITE, DEVICE], cli=transport_only(), scopes=("account.verify.relay",)),
)
async def get_verification_code(data: JSONBody[User], accounts: Accounts, _caller: NamedDependency[Caller]) -> int:
    """Generate a verification code for a user."""
    return await accounts.generate_verification_code(data.uuid)


@post(
    "/verify",
    status_code=201,
    responses=responses(401, 403, 404, 409, 422, 429, 503),
    dependencies={"_caller": Provide(requires(ACCOUNT_VERIFY_RELAY))},
    before_request=reserve_idempotent_before_request,
    operation_id="verification_create_compatibility",
    opt=contract(
        security=[SERVICE, WEB_WRITE, DEVICE],
        cli=compatibility_alias("verification_create"),
        scopes=("account.verify.relay",),
    ),
)
async def get_verification_code_compatibility(
    data: JSONBody[User],
    accounts: Accounts,
    _caller: NamedDependency[Caller],
) -> int:
    return await accounts.generate_verification_code(data.uuid)


def create_api_app(
    runtime_factory: RuntimeFactory = create_api_runtime,
    *,
    config: ApiProcessConfig | None = None,
    config_factory: ConfigFactory = load_api_process_config,
) -> Litestar:
    """Create the API and register its routes, middleware, and lifecycle.

    Configuration and services are loaded when the ASGI lifespan starts. The
    lifespan owns the runtime, background tasks, and rate limiter, and closes
    them on shutdown. Passing factories lets tests supply their own services.
    """

    @asynccontextmanager
    async def lifespan(app: Litestar):
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

    cors_origins = config.api.cors_origins if config is not None else ()
    cors_config = (
        CORSConfig(
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
        if cors_origins
        else None
    )
    api = Litestar(
        route_handlers=[
            live,
            ready,
            ready_compatibility,
            get_verification_code,
            get_verification_code_compatibility,
            v1_router,
        ],
        dependencies=service_dependencies(),
        plugins=[ApplicationValueSchemaPlugin()],
        lifespan=[lifespan],
        logging_config=None,
        exception_handlers=exception_handlers(),
        after_exception=[capture_exception],
        cors_config=cors_config,
        request_max_body_size=None,
        openapi_config=OpenAPIConfig(
            title="Redstone Squid API",
            version="1.0.0",
            description="Versioned public API for the Redstone Squid build catalog.",
            tags=[Tag(name=tag["name"], description=tag["description"]) for tag in TAGS_METADATA],
            use_handler_docstrings=True,
            create_examples=False,
            render_plugins=[ContractJsonRenderPlugin(), ContractSwaggerRenderPlugin()],
        ),
        middleware=[
            DefineMiddleware(LocaleContextMiddleware),
            DefineMiddleware(RequestContextMiddleware),
            DefineMiddleware(PrivateResponseHeadersMiddleware, path_prefixes=PRIVATE_API_PATH_PREFIXES),
            DefineMiddleware(BoundedRequestBodyMiddleware),
            DefineMiddleware(CompleteIdempotentResponseMiddleware),
            DefineMiddleware(RateLimitMiddleware),
        ],
    )
    install_openapi_contract(api)
    assert_idempotency_completion_installed(api)
    if config is not None:
        instrument_api_app(api, config.observability)
    return api


def _run_api(config: ApiProcessConfig) -> None:
    """Serve the configured API process until uvicorn stops."""
    uvicorn.run(
        create_api_app(config=config),
        host="0.0.0.0",
        port=config.api.port,
        log_config=None,
        proxy_headers=bool(config.api.trusted_proxy_ips),
        forwarded_allow_ips=list(config.api.trusted_proxy_ips),
    )


def main(process_config: ApiProcessConfig | None = None) -> None:
    """Run the Litestar server with process-owned logging and telemetry."""
    resolved_config = process_config or load_or_exit(load_api_process_config)
    configure_api_logging(resolved_config.logging, dev_mode=resolved_config.development_mode)
    observability = configure_observability(resolved_config.observability, service_name="api")
    try:
        _run_api(resolved_config)
    finally:
        observability.shutdown()


if __name__ == "__main__":
    main()
